"""Filtr pomiarów MDD z progami zamrożonymi przed walidacją i testem."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .branch_data import join_weather_asof, sum_branch_measurements

BASE = ["oddzial_code", "klient_nazwa", "kierunek_energii_norm"]
LEVELS = [(1, [*BASE, "miesiac", "godzina"]), (2, [*BASE, "miesiac"]),
          (3, [*BASE, "godzina"]), (4, BASE)]
THRESHOLD_COLUMNS = [*BASE, "miesiac", "godzina", "poziom", "n_referencji", "q1", "q3", "prog_gorny"]


@dataclass(frozen=True)
class EnergyFilterSettings:
    exclude_negative_energy: bool = True
    exclude_energy_outliers: bool = True
    energy_outlier_iqr_multiplier: float = 6.0
    energy_outlier_min_ratio: float = 3.0
    energy_outlier_min_samples: int = 30

    def validate(self):
        if not np.isfinite(self.energy_outlier_iqr_multiplier) or self.energy_outlier_iqr_multiplier <= 0:
            raise ValueError("energy_outlier_iqr_multiplier musi być dodatni i skończony.")
        if not np.isfinite(self.energy_outlier_min_ratio) or self.energy_outlier_min_ratio < 1:
            raise ValueError("energy_outlier_min_ratio musi być skończony i >= 1.")
        if not isinstance(self.energy_outlier_min_samples, int) or self.energy_outlier_min_samples < 4:
            raise ValueError("energy_outlier_min_samples musi być liczbą całkowitą >= 4.")


def filter_settings_from_options(options: dict) -> EnergyFilterSettings:
    defaults = asdict(EnergyFilterSettings())
    result = EnergyFilterSettings(**{key: options.get(key, value) for key, value in defaults.items()})
    result.validate()
    return result


def _keys(frame, timezone):
    result = frame.copy()
    local = result.target_time_utc.dt.tz_convert(timezone)
    result["miesiac"], result["godzina"] = local.dt.month, local.dt.hour
    if "wartosc_przed_filtracja" not in result:
        result["wartosc_przed_filtracja"] = result.wartosc_rzeczywista.copy()
    if "powod_wykluczenia_energii" not in result:
        result["powod_wykluczenia_energii"] = ""
    return result


def training_split_cutoff(energy: pd.DataFrame, weather: pd.DataFrame, settings) -> pd.Timestamp:
    """Ten sam ostatni wspólny cel co historical_requests, bez mnożenia godzin.

    Jeśli pogoda była dostępna dla najpóźniejszego dopuszczalnego origin, istniało
    zlecenie treningowe. Jeśli nie, nie była dostępna dla żadnego wcześniejszego.
    """
    frame = energy.loc[energy.wartosc_rzeczywista.notna()].copy()
    if frame.empty:
        raise ValueError("Brak wiarygodnej energii po wyłączeniu ujemnych pomiarów.")
    first = energy.target_time_utc.min().floor("D")
    hours = (frame.target_time_utc - first).dt.total_seconds() / 3600
    origin_hours = np.floor((hours - 1) / settings.origin_step_hours) * settings.origin_step_hours
    frame["forecast_origin_utc"] = first + pd.to_timedelta(origin_hours, unit="h")
    frame["horyzont_h"] = hours - origin_hours
    frame = frame.loc[frame.forecast_origin_utc.ge(first) & frame.horyzont_h.between(1, settings.horizon_hours)]
    joined = join_weather_asof(frame, weather)
    targets = joined.loc[joined.pogoda_dopasowana, "target_time_utc"]
    if targets.empty:
        raise ValueError("Brak wspólnej historii energii i pogody dostępnej przed origin do kalibracji filtra.")
    return targets.max().floor("D")


def fit_energy_filter(measurements, reference_before, settings: EnergyFilterSettings, timezone):
    settings.validate()
    # Duże eksporty MDD mają miliony wierszy; kalibracja nie potrzebuje kopii
    # opisów źródłowych, arkuszy i pozostałych kolumn całego eksportu.
    columns = [*BASE, "target_time_utc", "wartosc_rzeczywista"]
    columns += [name for name in ["wartosc_przed_filtracja", "powod_wykluczenia_energii"] if name in measurements]
    reference = _keys(measurements.loc[measurements.target_time_utc.lt(reference_before), columns], timezone)
    values = reference.wartosc_przed_filtracja
    # Same zera nie dowodzą, że późniejsza produkcja PV jest błędem. Nie uczymy
    # górnego progu z zer ani z niewiarygodnych/ujemnych wartości.
    reference = reference.loc[values.gt(0) & np.isfinite(values) & reference.powod_wykluczenia_energii.eq("")]
    tables = []
    if settings.exclude_energy_outliers:
        for level, keys in LEVELS:
            groups = reference.groupby(keys, observed=True).wartosc_przed_filtracja
            quartiles = groups.quantile([.25, .75]).unstack().rename(columns={.25: "q1", .75: "q3"})
            # Pusta referencja ma pusty indeks kwantyli; zachowujemy jawny schemat.
            if quartiles.empty:
                continue
            quartiles["n_referencji"] = groups.count()
            quartiles = quartiles.loc[quartiles.n_referencji.ge(settings.energy_outlier_min_samples)].copy()
            quartiles["prog_gorny"] = np.maximum(
                quartiles.q3 + settings.energy_outlier_iqr_multiplier * (quartiles.q3 - quartiles.q1),
                quartiles.q3 * settings.energy_outlier_min_ratio)
            quartiles = quartiles.loc[np.isfinite(quartiles.prog_gorny)].reset_index()
            quartiles["poziom"] = level
            for column in ["miesiac", "godzina"]:
                if column not in quartiles:
                    quartiles[column] = -1
            tables.append(quartiles[THRESHOLD_COLUMNS])
    thresholds = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=THRESHOLD_COLUMNS)
    return {"version": 1, "settings": asdict(settings), "reference_before_utc": str(reference_before),
            "n_dodatnich_referencji": len(reference), "thresholds": thresholds.to_dict("records"),
            "method": "Dodatnie pomiary: górny próg max(Q3+k*IQR, Q3*min_ratio). Zero zachowane; brak pewnej referencji nie usuwa pomiaru."}


def apply_energy_filter(measurements, state: dict, timezone: str):
    settings = EnergyFilterSettings(**state["settings"])
    settings.validate()
    frame = _keys(measurements, timezone)
    frame["wartosc_rzeczywista"] = frame.wartosc_przed_filtracja.copy()
    frame["powod_wykluczenia_energii"] = ""
    negative = frame.wartosc_przed_filtracja.lt(0).fillna(False)
    if negative.any() and not settings.exclude_negative_energy:
        raise ValueError("Pobranie/oddanie ma ujemną wartość. Wyłącz tryb ścisły lub wyjaśnij pomiary.")
    frame.loc[negative, "powod_wykluczenia_energii"] = "UJEMNY_POMIAR"
    frame["prog_gorny_energii"] = np.nan
    frame["poziom_progu_energii"] = pd.Series(pd.NA, index=frame.index, dtype="Int64")
    table = pd.DataFrame(state["thresholds"], columns=THRESHOLD_COLUMNS)
    if settings.exclude_energy_outliers and not table.empty:
        for level, keys in LEVELS:
            available = table.loc[table.poziom.eq(level)].set_index(keys).prog_gorny
            if available.empty:
                continue
            limits = available.reindex(pd.MultiIndex.from_frame(frame[keys])).to_numpy(dtype=float)
            assign = frame.prog_gorny_energii.isna() & np.isfinite(limits)
            frame.loc[assign, "prog_gorny_energii"] = limits[assign]
            frame.loc[assign, "poziom_progu_energii"] = level
    outlier = frame.wartosc_przed_filtracja.gt(frame.prog_gorny_energii) & frame.powod_wykluczenia_energii.eq("")
    frame.loc[outlier, "powod_wykluczenia_energii"] = "ODSTAJACY_POMIAR_POWYZEJ_PROGU"
    discarded = frame.powod_wykluczenia_energii.ne("")
    frame.loc[discarded, "wartosc_rzeczywista"] = np.nan
    frame["status_progu_energii"] = np.where(frame.prog_gorny_energii.notna(), "PROG_Z_HISTORII_TRENINGOWEJ", "BRAK_WYSTARCZAJACEJ_REFERENCJI")
    if not settings.exclude_energy_outliers:
        frame["status_progu_energii"] = "FILTR_ODSTAJACYCH_WYLACZONY"
    columns = [column for column in ["source_sheet", "source_row", *BASE, "rodzaj_rank", "target_time_utc",
               "doba_handlowa", "godzina_handlowa", "wartosc_przed_filtracja", "prog_gorny_energii",
               "poziom_progu_energii", "powod_wykluczenia_energii"] if column in frame]
    rejected = frame.loc[discarded, columns].copy()
    local = frame.target_time_utc.dt.tz_convert(timezone)
    counts = frame.assign(rok_miesiac=local.dt.strftime("%Y-%m"),
                          powod=frame.powod_wykluczenia_energii.replace("", "POMIAR_NIEWYKLUCZONY"))
    summary = counts.groupby(["oddzial_code", "kierunek_energii_norm", "rok_miesiac", "powod"], observed=True).size().rename("liczba_pomiarow").reset_index()
    unchecked = frame.wartosc_przed_filtracja.gt(0) & frame.prog_gorny_energii.isna() & settings.exclude_energy_outliers
    control = pd.DataFrame([
        ["ujemne_pomiary_wykluczone", int(negative.sum()), "nie odwrócono znaku i nie wybrano starszej korekty"],
        ["odstajace_pomiary_wykluczone", int(outlier.sum()), "próg statystyczny zamrożony przed walidacją; nie dowodzi błędu źródła"],
        ["dodatnie_pomiary_bez_progu", int(unchecked.sum()), "nowe MDD lub za mała referencja; pozostawione, nie zgadujemy progu innych klientów"],
        ["referencja_filtra_przed_UTC", 0, state["reference_before_utc"]],
    ], columns=["kontrola", "liczba", "szczegoly"])
    reports = {"odrzucone_pomiary_energii": rejected, "podsumowanie_filtra_energii": summary,
               "progi_odstajacych_energii": table, "kontrola_filtra_energii": control}
    return frame, reports


def prepare_training_energy(energy, measurements, weather, model_settings, filter_settings):
    cutoff = training_split_cutoff(energy, weather, model_settings)
    reference_before = cutoff - pd.Timedelta(days=model_settings.validation_days + model_settings.test_days,
                                            hours=model_settings.label_delay_hours)
    state = fit_energy_filter(measurements, reference_before, filter_settings, model_settings.timezone)
    filtered, reports = apply_energy_filter(measurements, state, model_settings.timezone)
    hourly = sum_branch_measurements(filtered, model_settings.timezone)
    incomplete = hourly.loc[hourly.liczba_odrzuconych_pomiarow.gt(0)]
    reports["godziny_wykluczone_filtrem"] = incomplete.copy()
    return hourly, filtered, state, reports, cutoff
