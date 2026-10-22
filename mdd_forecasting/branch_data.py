"""Dane do prognoz oddziałów: korekty pomiarów i historyczne wydania pogody."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ENERGY_DIRECTIONS, PipelineConfig, WEATHER_FEATURES
from .io import (
    _ascii_token, _numeric, load_branch_mapping, normalize_energy,
    normalize_weather, read_energy_file,
)

# Aliasy potwierdzone na zdjęciach i w opisie. ŁZE oraz ZEUE to Łódź miasto.
BRANCH_ALIASES = {"LBU": "LUB", "ŁZE": "ZEUE", "LZE": "ZEUE", "ZŁT": "ZEUD", "ZLT": "ZEUD", "RZ": "RZE"}
BRANCHES = ("BIA", "LUB", "RZE", "SKO", "WAW", "ZAM", "ZEUD", "ZEUE")


class EnergyTimeError(ValueError):
    """Błąd podstawowej walidacji czasu z przykładami rekordów źródłowych."""

    def __init__(self, message: str, records: pd.DataFrame):
        super().__init__(message)
        self.records = records


def utc_timestamp(value: object, timezone: str = "Europe/Warsaw") -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        raise ValueError("Czas prognozy nie może być pusty.")
    if ts.tzinfo is None:
        ts = ts.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
    return ts.tz_convert("UTC").as_unit("ns")


def trade_time_utc(dates: pd.Series, hours: pd.Series, timezone: str) -> pd.Series:
    """1=00:00; 25=dodatkowa 02:00 zimowa. Wiosenna 03 jest niepoprawna.

    Konwencja eksportu energii, poza domyślnie wyłączonymi dobami DST.
    Nie utożsamiamy 25. godziny z północą następnego dnia. Pogoda używa
    własnych znaczników UTC, niezależnie od swojej numeracji handlowej.
    """
    h = pd.to_numeric(hours, errors="coerce")
    normal = h.between(1, 24) & h.mod(1).eq(0)
    valid = pd.to_datetime(dates, errors="coerce").dt.normalize()
    local = valid + pd.to_timedelta(h.where(normal, 3) - 1, unit="h")
    summer = local.dt.tz_localize(timezone, ambiguous=True, nonexistent="NaT")
    winter = local.dt.tz_localize(timezone, ambiguous=False, nonexistent="NaT")
    repeated = summer.ne(winter) & summer.notna() & winter.notna()
    result = summer.dt.tz_convert("UTC").where(normal)
    extra = h.eq(25) & repeated
    result.loc[extra] = winter.loc[extra].dt.tz_convert("UTC")
    return result


def _from_start_date(frame: pd.DataFrame, start_date: object | None):
    if start_date is None:
        return frame, 0
    cutoff = pd.Timestamp(start_date)
    if pd.isna(cutoff) or cutoff.tzinfo is not None:
        raise ValueError("start_date musi być poprawną lokalną datą bez strefy czasowej.")
    # NaT nie potwierdza, że rekord jest poza zakresem: musi trafić do walidacji.
    keep = frame["doba_handlowa"].isna() | frame["doba_handlowa"].ge(cutoff.normalize())
    return frame.loc[keep].copy(), int((~keep).sum())


def _without_dst_days(frame: pd.DataFrame, timezone: str):
    """Wyłącz całe lokalne doby inne niż 24 h, zachowując NaT do walidacji.

    Reguła jest kalendarzowa: nie zależy od obecności H25 ani kompletności MDD.
    Raport obejmuje dni zmiany czasu w zakresie danych, także z zerem rekordów.
    """
    dates = frame["doba_handlowa"]
    calendar = pd.DatetimeIndex([]) if dates.notna().sum() == 0 else pd.date_range(dates.min(), dates.max(), freq="D")
    midnight = calendar.tz_localize(timezone)
    next_midnight = (calendar + pd.Timedelta(days=1)).tz_localize(timezone)
    hours = (next_midnight.tz_convert("UTC") - midnight.tz_convert("UTC")) / pd.Timedelta(hours=1)
    report = pd.DataFrame({"doba_handlowa": calendar, "godziny_doby": hours})
    report = report.loc[report.godziny_doby.ne(24)].copy()
    counts = dates.value_counts()
    report["rekordy_wykluczone"] = report.doba_handlowa.map(counts).fillna(0).astype(int)
    excluded = dates.isin(report.doba_handlowa)
    if not report.empty:
        description = ", ".join(f"{row.doba_handlowa:%Y-%m-%d} ({row.godziny_doby:g} h)" for row in report.itertuples())
        print(f"Pomijam doby zmiany czasu: {description}. Wyłączone rekordy: {int(excluded.sum())}.", flush=True)
    return frame.loc[~excluded].copy(), report.reset_index(drop=True)


def _empty_dst_report():
    return pd.DataFrame(columns=["doba_handlowa", "godziny_doby", "rekordy_wykluczone"])


def load_branch_energy(path: str | Path, config: PipelineConfig, return_measurements: bool = False,
                       *, start_date: object | None = None, exclude_dst_days: bool = False,
                       return_excluded_days: bool = False, end_date: object | None = None,
                       exclude_negative_energy: bool = False):
    """Opcjonalny raport wyłączonych dób jest ostatnim elementem wyniku."""
    mapping, mapping_table = load_branch_mapping(config.mapping_path)
    raw = read_energy_file(path, sheet_regex=config.sheet_regex)
    normalized, quality = normalize_energy(raw, mapping)
    normalized, excluded = _from_start_date(normalized, start_date)
    if end_date is not None:
        cutoff = pd.Timestamp(end_date)
        if pd.isna(cutoff) or cutoff.tzinfo is not None:
            raise ValueError("end_date musi być poprawną lokalną datą bez strefy czasowej.")
        keep = normalized.doba_handlowa.isna() | normalized.doba_handlowa.le(cutoff.normalize())
        count = int((~keep).sum())
        normalized = normalized.loc[keep].copy()
        quality = pd.concat([quality, pd.DataFrame([{
            "kontrola": "po_dacie_koncowej", "liczba": count,
            "szczegoly": f"poza zakresem do {cutoff.date()}; wyłączone przed walidacją i agregacją",
        }])], ignore_index=True)
    if start_date is not None:
        quality = pd.concat([quality, pd.DataFrame([{
            "kontrola": "przed_data_startowa", "liczba": excluded,
            "szczegoly": f"poza wybranym zakresem od {start_date}; wyłączone przed walidacją i agregacją",
        }])], ignore_index=True)
    excluded_days = _empty_dst_report()
    if exclude_dst_days:
        normalized, excluded_days = _without_dst_days(normalized, config.timezone)
        quality = pd.concat([quality, pd.DataFrame([{
            "kontrola": "rekordy_wykluczone_zmiana_czasu",
            "liczba": int(excluded_days.rekordy_wykluczone.sum()),
            "szczegoly": "całe doby 23/25 h przed walidacją i agregacją; szczegóły w pominiete_dni_zmiany_czasu.csv",
        }, {
            "kontrola": "wiersze_po_filtrach", "liczba": len(normalized),
            "szczegoly": "zakres po start_date i wyłączeniu dób zmiany czasu; bez uzupełniania zerami",
        }])], ignore_index=True)
    hourly, extra, measurements = aggregate_branch_energy(
        normalized, config.timezone, return_measurements=True, mapping_path=config.mapping_path,
        exclude_negative_energy=exclude_negative_energy,
    )
    result = (hourly, pd.concat([quality, extra], ignore_index=True), mapping_table)
    if return_measurements:
        result = (*result, measurements)
    return (*result, excluded_days) if return_excluded_days else result


def load_prediction_measurements(path: str | Path, bundle: dict, *, return_excluded_days: bool = False,
                                 return_filter_reports: bool = False):
    # Przy predykcji używamy mapowania zapisanego z modelem, nie zmienionego słownika.
    mapping = {_ascii_token(row.oddzial_code): row.punkt for row in bundle["branches"].itertuples()}
    for alias, canonical in BRANCH_ALIASES.items():
        if _ascii_token(canonical) in mapping:
            mapping[_ascii_token(alias)] = mapping[_ascii_token(canonical)]
    raw = read_energy_file(path, sheet_regex=bundle.get("input_schema", {}).get("sheet_regex", PipelineConfig().sheet_regex))
    normalized, _ = normalize_energy(raw, mapping)
    start = bundle.get("input_schema", {}).get("start_date")
    normalized, _ = _from_start_date(normalized, start)
    excluded_days = _empty_dst_report()
    if bundle.get("input_schema", {}).get("exclude_dst_days", False):
        normalized, excluded_days = _without_dst_days(normalized, bundle["settings"]["timezone"])
    _, _, measurements = aggregate_branch_energy(
        normalized, bundle["settings"]["timezone"], return_measurements=True,
        exclude_negative_energy=bundle.get("input_schema", {}).get("exclude_negative_energy", False))
    reports = {}
    if bundle.get("energy_filter"):
        from .energy_filter import apply_energy_filter
        measurements, reports = apply_energy_filter(measurements, bundle["energy_filter"], bundle["settings"]["timezone"])
    if return_filter_reports:
        return measurements, excluded_days, reports
    return (measurements, excluded_days) if return_excluded_days else measurements


def _within_day_gaps(measurements: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Luki między pomiarami tego samego MDD w tej samej dobie; bez domyślania umów.

    Nie rozszerzamy oczekiwanej obecności przed pierwszy ani po ostatni pomiar.
    Dłuższe przerwy między dobami opisuje odrębny audyt portfela.
    """
    keys = ["oddzial_code", "punkt", "target_time_utc", "doba_handlowa", "godzina_handlowa", "kierunek_energii_norm"]
    scope = ["oddzial_code", "punkt", "klient_nazwa", "kierunek_energii_norm", "doba_handlowa"]
    ordered = measurements.sort_values([*scope, "target_time_utc"])
    previous = ordered.groupby(scope, dropna=False)["target_time_utc"].shift()
    difference = (ordered["target_time_utc"] - previous).dt.total_seconds().div(3600)
    rows = []
    for index in difference.loc[difference.gt(1)].index:
        row = ordered.loc[index]
        for time in pd.date_range(previous.loc[index] + pd.Timedelta(hours=1),
                                  row["target_time_utc"] - pd.Timedelta(hours=1), freq="h"):
            local = time.tz_convert(timezone)
            wall = local.tz_localize(None)
            first = wall.tz_localize(timezone, ambiguous=True)
            hour = 25 if local.hour == 2 and first.tz_convert("UTC") != time else local.hour + 1
            rows.append({"oddzial_code": row["oddzial_code"], "punkt": row["punkt"],
                         "target_time_utc": time, "doba_handlowa": row["doba_handlowa"],
                         "godzina_handlowa": hour, "kierunek_energii_norm": row["kierunek_energii_norm"],
                         "klient_nazwa": row["klient_nazwa"]})
    if not rows:
        return pd.DataFrame(columns=[*keys, "brakujace_rekordy_mdd"])
    return pd.DataFrame(rows).groupby(keys, as_index=False)["klient_nazwa"].nunique().rename(
        columns={"klient_nazwa": "brakujace_rekordy_mdd"})


def _energy_examples(frame: pd.DataFrame, mask) -> list[dict]:
    columns = [col for col in [
        "source_sheet", "source_row", "oddzial_code", "klient_nazwa",
        "kierunek_code_zrodlowy", "kierunek_code", "kierunek_energii",
        "doba_handlowa_zrodlowa", "godzina_handlowa_zrodlowa",
        "doba_handlowa", "godzina_handlowa", "powod_bledu_czasu", "rodzaj", "wartosc_rzeczywista",
    ] if col in frame]
    return frame.loc[mask, columns].head(5).to_dict("records")


def aggregate_branch_energy(frame: pd.DataFrame, timezone: str = "Europe/Warsaw", return_measurements: bool = False,
                            *, mapping_path: str | Path | None = None, exclude_negative_energy: bool = False):
    df = frame.copy()
    unknown = sorted(df.loc[df["oddzial_code"].notna() & df["punkt"].isna(), "oddzial_code"].unique())
    if unknown:
        keys = {code: _ascii_token(code) for code in unknown}
        source = str(Path(mapping_path).expanduser().resolve()) if mapping_path else "oddzial_miasto.csv"
        columns = [col for col in ["source_sheet", "source_row", "oddzial_code"] if col in df]
        examples = df.loc[df["punkt"].isna() & df["oddzial_code"].notna(), columns].head(5).to_dict("records")
        raise ValueError(
            f"Brak mapowania pogody dla oddziałów {unknown}. Klucze po normalizacji: {keys}. "
            f"Przykładowe rekordy: {examples}. Uzupełnij {source}: "
            "oddzial_code = kod z eksportu, punkt = potwierdzone miasto pogody, status = active. "
            "Sprawdź też wpisy inactive. Nieznane oddziały nie są pomijane ani przypisywane automatycznie."
        )
    df["oddzial_code"] = df["oddzial_code"].str.upper().replace(BRANCH_ALIASES)
    # Wiersze z pustym oddziałem są poza portfelem oddziałów.
    relevant = df["oddzial_code"].notna()
    df = df.loc[relevant].copy()
    invalid_direction = ~df["kierunek_code"].isin(ENERGY_DIRECTIONS)
    if invalid_direction.any():
        raise ValueError(
            "Niepoprawny kierunek energii w kolumnie B. Wymagane: 1 = czynne pobranie, "
            "-1 = czynne oddanie. Brak i inne wartości wymagają wyjaśnienia; "
            "opis z E nie zastępuje kodu B. "
            f"Liczba rekordów: {int(invalid_direction.sum())}. "
            f"Przykłady: {_energy_examples(df, invalid_direction)}"
        )
    df["kierunek_energii_norm"] = df["kierunek_code"].map(ENERGY_DIRECTIONS).astype("string")
    if df["klient_nazwa"].isna().any():
        raise ValueError("Brak nazwy MDD: nie można bezpiecznie wybrać korekt pomiaru.")
    df["rodzaj_rank"] = _numeric(df["rodzaj"])
    if (~df["rodzaj_rank"].isin(range(5))).any():
        raise ValueError("Rodzaj danych musi być liczbą od 0 do 4.")
    df["target_time_utc"] = trade_time_utc(df["doba_handlowa"], df["godzina_handlowa"], timezone)
    invalid_time = df["target_time_utc"].isna()
    if invalid_time.any():
        problems = df.loc[invalid_time].copy()
        h = problems["godzina_handlowa"]
        invalid_hour = (~(h.between(1, 25) & h.mod(1).eq(0))).fillna(True)
        problems["powod_bledu_czasu"] = np.select(
            [problems["doba_handlowa"].isna().to_numpy(dtype=bool), invalid_hour.to_numpy(dtype=bool), h.eq(25).fillna(False).to_numpy(dtype=bool)],
            ["nierozpoznana lub pusta data F", "H musi być liczbą całkowitą od 1 do 25",
             "godzina 25 poza dodatkową jesienną godziną zmiany czasu"],
            default="lokalna godzina nie istnieje podczas wiosennej zmiany czasu",
        )
        raise EnergyTimeError(
            f"Niepoprawna data/godzina energii. Liczba rekordów: {len(problems)}. "
            f"Powody: {problems['powod_bledu_czasu'].value_counts().to_dict()}. "
            f"Przykłady z oryginalnymi F/H i numerami wierszy: {_energy_examples(problems, slice(None))}. "
            "Nie usunięto tych rekordów. Sprawdź format dat F i numerację godzin H (23/25 przy zmianie czasu).",
            problems,
        )
    measurement = ["oddzial_code", "klient_nazwa", "kierunek_energii_norm", "target_time_utc"]
    highest = df.groupby(measurement, dropna=False)["rodzaj_rank"].transform("max")
    best = df.loc[df["rodzaj_rank"].eq(highest)].copy()
    conflicts = best.groupby(measurement, dropna=False)["wartosc_rzeczywista"].nunique(dropna=False)
    if conflicts.gt(1).any():
        conflict_keys = conflicts.loc[conflicts.gt(1)].index
        conflicting = best.set_index(measurement).index.isin(conflict_keys)
        raise ValueError(
            "Ten sam pomiar MDD ma różne wartości w tej samej najlepszej wersji danych. "
            "Kierunek ustala B; różne opisy E nie tworzą osobnych pomiarów. "
            f"Przykłady: {_energy_examples(best, conflicting)}"
        )
    best = best.drop_duplicates(measurement, keep="last")
    if best["wartosc_rzeczywista"].isin([np.inf, -np.inf]).any():
        raise ValueError("Pomiar energii musi być skończoną liczbą albo brakiem wartości.")
    negative = best.wartosc_rzeczywista.lt(0).fillna(False)
    if negative.any() and not exclude_negative_energy:
        raise ValueError("Pobranie/oddanie ma ujemną wartość. Znaku nie odwracamy automatycznie.")
    best["wartosc_przed_filtracja"] = best.wartosc_rzeczywista.copy()
    best["powod_wykluczenia_energii"] = np.where(negative, "UJEMNY_POMIAR", "")
    best.loc[negative, "wartosc_rzeczywista"] = np.nan
    hourly = sum_branch_measurements(best, timezone)
    extra = pd.DataFrame([
        ["poza_oddzialami", int((~relevant).sum()), "puste A; pomiary nie zostały przypisane na podstawie poprzedniego wiersza"],
        ["odrzucone_korekty_i_duplikaty", len(df) - len(best), "wybrano najwyższy rodzaj 4 > 3 > 2 > 1 > 0"],
        ["odrzucone_ujemne_pomiary", int(negative.sum()), "po wyborze korekt; bez odwracania znaku, zastępowania zerem i użycia starszej wersji"],
        ["niepelne_sumy_oddzialu", int(hourly.wartosc_rzeczywista.isna().sum()), "co najmniej jeden pomiar MDD nie ma wiarygodnej wartości"],
        ["luki_godzinowe_mdd_w_dobie", int(hourly["brakujace_rekordy_mdd"].sum()), "brak rekordu między pomiarami tego samego MDD w tej samej dobie; suma pozostaje brakiem"],
        ["historia_korekt_bez_czasu_dostepnosci", len(best), "końcowe pomiary są targetem; nie są domyślnie używane jako lagi"],
    ], columns=["kontrola", "liczba", "szczegoly"])
    result = (hourly, extra)
    return (*result, best) if return_measurements else result


def sum_branch_measurements(measurements: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Sumuj wybrane korekty; wykluczony składnik uniemożliwia wiarygodną sumę."""
    best = measurements.copy()
    if "powod_wykluczenia_energii" not in best:
        best["powod_wykluczenia_energii"] = ""
    best["_wykluczony"] = best.powod_wykluczenia_energii.ne("").astype(int)
    keys = ["oddzial_code", "punkt", "target_time_utc", "doba_handlowa", "godzina_handlowa", "kierunek_energii_norm"]
    grouped = best.groupby(keys, dropna=False)
    hourly = grouped.agg(
        wartosc_rzeczywista=("wartosc_rzeczywista", "sum"),
        liczba_mdd=("klient_nazwa", "nunique"),
        liczba_wartosci=("wartosc_rzeczywista", "count"),
        rodzaj_min=("rodzaj_rank", "min"), rodzaj_max=("rodzaj_rank", "max"),
        liczba_odrzuconych_pomiarow=("_wykluczony", "sum"),
    ).reset_index()
    gaps = _within_day_gaps(best, timezone)
    if not gaps.empty:
        hourly = hourly.merge(gaps, on=keys, how="outer", validate="one_to_one")
    else:
        hourly["brakujace_rekordy_mdd"] = 0
    hourly["brakujace_rekordy_mdd"] = hourly["brakujace_rekordy_mdd"].fillna(0).astype(int)
    hourly["liczba_odrzuconych_pomiarow"] = hourly["liczba_odrzuconych_pomiarow"].fillna(0).astype(int)
    incomplete = hourly["liczba_wartosci"].ne(hourly["liczba_mdd"]) | hourly["brakujace_rekordy_mdd"].gt(0)
    hourly.loc[incomplete, "wartosc_rzeczywista"] = np.nan
    hourly["status_jakosci_energii"] = np.select(
        [hourly.liczba_odrzuconych_pomiarow.gt(0), incomplete],
        ["POMIARY_WYKLUCZONE_FILTREM", "BRAK_PELNEJ_SUMY"], default="OK")
    return hourly.sort_values(["target_time_utc", "oddzial_code", "kierunek_energii_norm"])


def _mark_weather_conflicts(weather: pd.DataFrame) -> pd.DataFrame:
    key = ["_punkt_key", "weather_valid_time_utc", "weather_issue_time_utc"]
    differences = weather.groupby(key)[WEATHER_FEATURES].nunique(dropna=False).gt(1)
    fields = pd.Series({idx: ", ".join(row.index[row])
                            for idx, row in differences.loc[differences.any(axis=1)].iterrows()},
                           index=differences.index, dtype="object").fillna("")
    weather = weather.merge(fields.rename("konflikt_pola").reset_index(), on=key,
                            how="left", validate="many_to_one")
    return weather


def prepare_weather_vintages(raw: pd.DataFrame, config: PipelineConfig) -> pd.DataFrame:
    from .weather_time import normalize_weather_time
    weather = normalize_weather(raw, replace(config, weather_already_vintaged=True,
                                            preserve_weather_vintages=True), utc_primary=True)
    weather = normalize_weather_time(weather, config.timezone)
    # Oznacz konflikty, ale rozstrzygaj je dopiero po wyborze wydania as-of.
    # Nieużywane wydania (starsze lub przyszłe) nie blokują całego archiwum.
    weather = _mark_weather_conflicts(weather)
    # Zachowaj oryginalne rekordy także do audytu metadanych. Identyczne
    # klucze i cechy są deduplikowane dopiero na wejściu łączenia as-of.
    return weather


def join_weather_asof(requests: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Każdy wiersz ma własny origin; przez całe 72 h cutoff pozostaje zamrożony."""
    left = requests.copy()
    left["_punkt_key"] = left["punkt"].map(_ascii_token)
    left["_request_order"] = np.arange(len(left))
    if "konflikt_pola" not in weather:
        # Także ramki utworzone poza importerem muszą przejść kontrolę remisu.
        weather = _mark_weather_conflicts(weather)
    columns = ["_punkt_key", "weather_valid_time_utc", "weather_issue_time_utc",
               "konflikt_pola", *WEATHER_FEATURES]
    right = weather[columns].drop_duplicates(
        ["_punkt_key", "weather_valid_time_utc", "weather_issue_time_utc"]
    ).rename(columns={"weather_valid_time_utc": "target_time_utc"})
    # Eksport CSV może dać nanosekundy, a Timestamp z CLI sekundy.
    # merge_asof wymaga identycznej rozdzielczości obu kluczy czasu.
    for column in ["target_time_utc", "forecast_origin_utc"]:
        left[column] = pd.to_datetime(left[column], utc=True).astype("datetime64[ns, UTC]")
    for column in ["target_time_utc", "weather_issue_time_utc"]:
        right[column] = pd.to_datetime(right[column], utc=True).astype("datetime64[ns, UTC]")
    right = right.sort_values("weather_issue_time_utc")
    joined = pd.merge_asof(
        left.sort_values("forecast_origin_utc"), right,
        left_on="forecast_origin_utc", right_on="weather_issue_time_utc",
        by=["_punkt_key", "target_time_utc"], direction="backward", allow_exact_matches=True,
    ).sort_values("_request_order").drop(columns="_request_order").reset_index(drop=True)
    conflicts = joined["konflikt_pola"].fillna("").ne("")
    if conflicts.any():
        print(f"Pogoda: pomijam {int(conflicts.sum())} z {len(joined)} dopasowań "
              "z konfliktem wybranego wydania. Szczegóły: konflikty_wersji_pogody.csv.", flush=True)
        # Pozostaw czas i przyczynę do audytu, ale usuń wszystkie cechy pogody.
        # Nie usuwaj wydania przed merge_asof: to wybrałoby starszą prognozę.
        joined.loc[conflicts, WEATHER_FEATURES] = np.nan
    joined["pogoda_konflikt"] = conflicts
    joined["pogoda_dopasowana"] = joined["weather_issue_time_utc"].notna() & ~conflicts
    joined["horyzont_h"] = (joined["target_time_utc"] - joined["forecast_origin_utc"]).dt.total_seconds() / 3600
    joined["wiek_prognozy_h"] = (joined["forecast_origin_utc"] - joined["weather_issue_time_utc"]).dt.total_seconds() / 3600
    return joined


def historical_requests(energy: pd.DataFrame, horizon: int = 72, step_hours: int = 12) -> pd.DataFrame:
    if horizon < 1 or step_hours < 1:
        raise ValueError("Horyzont i odstęp uruchomień muszą być dodatnie.")
    start = energy["target_time_utc"].min().floor("D")
    end = energy["target_time_utc"].max()
    origins = pd.date_range(start, end, freq=f"{step_hours}h", tz="UTC")
    pieces = []
    for offset in range(1, horizon + 1):
        schedule = pd.DataFrame({"forecast_origin_utc": origins,
                                 "target_time_utc": origins + pd.Timedelta(hours=offset)})
        pieces.append(energy.merge(schedule, on="target_time_utc", how="inner", validate="many_to_one"))
    return pd.concat(pieces, ignore_index=True)


def future_requests(branch_table: pd.DataFrame, origin: object, horizon: int, timezone: str):
    asof = utc_timestamp(origin, timezone)
    if not 1 <= horizon <= 168:
        raise ValueError("Horyzont musi należeć do zakresu 1–168 godzin.")
    times = pd.date_range(asof.floor("h") + pd.Timedelta(hours=1), periods=horizon, freq="h", tz="UTC")
    local = times.tz_convert(timezone)
    hours = local.hour + 1
    # Druga lokalna 02:00 jesienią ma numer handlowy 25.
    wall = local.tz_localize(None)
    second_02 = wall.hour == 2
    second_02 &= np.array([x.dst().total_seconds() == 0 for x in local])
    first_version = wall.tz_localize(timezone, ambiguous=True, nonexistent="NaT")
    second_02 &= first_version.tz_convert("UTC") != times
    hours = np.where(second_02, 25, hours)
    grid = pd.DataFrame({"target_time_utc": times, "doba_handlowa": wall.normalize(),
                         "godzina_handlowa": hours, "forecast_origin_utc": asof})
    return branch_table[["oddzial_code", "punkt"]].drop_duplicates().merge(grid, how="cross")
