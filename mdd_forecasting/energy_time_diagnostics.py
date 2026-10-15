"""Audyt F/H bez agregacji, SQL i zmiany interpretacji czasu w modelu."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .branch_data import trade_time_utc
from .config import PipelineConfig
from .io import _clean_text, _numeric, read_energy_file


@dataclass
class HourDiagnostics:
    summary: str
    tables: dict[str, pd.DataFrame]


def _hours_text(values) -> str:
    return ", ".join(str(int(value)) for value in sorted(set(values)))


def _match(observed: set[int], expected: set[int]) -> str:
    if observed == expected:
        return "PELNY_WZORZEC"
    return "NIEPELNY_ZGODNY" if observed < expected else "NIEZGODNY"


def diagnose_energy_hours(raw: pd.DataFrame, timezone: str = "Europe/Warsaw") -> HourDiagnostics:
    """Porównaj wzorce etykiet; zgodność nie potwierdza semantyki eksportu.

    Wzorzec dotyczy jednej doby, oddziału, MDD, B i wersji G. Nie łączymy
    brakujących godzin różnych MDD/kierunków/korekt w pozornie pełną dobę.
    Parser F/H jest taki sam jak w normalize_energy; oryginały zostają w CSV.
    """
    dates = pd.to_datetime(raw["doba_handlowa"], errors="coerce").dt.normalize()
    hours = _numeric(raw["godzina_handlowa"]).astype(float)
    values = _numeric(raw["wartosc_rzeczywista"]).astype(float)
    direction = _numeric(raw["kierunek_code"]).astype(float)
    revision = _numeric(raw["rodzaj"]).astype(float)
    valid_h = hours.between(1, 25) & hours.mod(1).eq(0)
    issues = pd.Series("", index=raw.index, dtype="string")
    checks = {
        "nierozpoznana_data_F": dates.isna(),
        "H_poza_calkowitym_1_25": ~valid_h,
        "B_inne_niz_1_lub_minus_1": ~direction.isin([1, -1]),
        "G_poza_0_4": ~revision.isin(range(5)),
        "brak_lub_niepoprawne_I": values.isna() | ~np.isfinite(values) | values.lt(0),
    }
    for reason, mask in checks.items():
        issues.loc[mask] += reason + "; "
    problems = raw.loc[issues.ne("")].copy()
    problems["powod_diagnostyczny"] = issues.loc[problems.index].str.rstrip("; ")
    problems["doba_odczytana"] = dates.loc[problems.index]
    problems["godzina_odczytana"] = hours.loc[problems.index]

    # Granice liczymy z lokalnych północy, nie przez dodanie 24 h po lokalizacji.
    calendar = pd.DatetimeIndex([]) if dates.notna().sum() == 0 else pd.date_range(dates.min(), dates.max(), freq="D")
    midnight = calendar.tz_localize(timezone)
    next_midnight = (calendar + pd.Timedelta(days=1)).tz_localize(timezone)
    lengths = ((next_midnight.tz_convert("UTC") - midnight.tz_convert("UTC")) / pd.Timedelta(hours=1)).astype(int)
    changed = pd.DataFrame({"doba_odczytana": calendar, "liczba_godzin_doby": lengths})
    changed = changed.loc[changed.liczba_godzin_doby.ne(24)].copy()
    changed["typ_doby"] = np.where(changed.liczba_godzin_doby.lt(24), "WIOSNA", "JESIEN")
    length_by_date = changed.set_index("doba_odczytana").liczba_godzin_doby
    selected = dates.isin(length_by_date.index)
    details = raw.loc[selected].copy()
    details["doba_odczytana"] = dates.loc[selected]
    details["godzina_odczytana"] = hours.loc[selected]
    details["wartosc_odczytana"] = values.loc[selected]
    details["liczba_godzin_doby"] = dates.loc[selected].map(length_by_date)
    details["powod_diagnostyczny"] = issues.loc[selected].str.rstrip("; ")

    work = pd.DataFrame({
        "doba_odczytana": dates.loc[selected],
        "oddzial_code": _clean_text(raw.loc[selected, "oddzial_code"]),
        "klient_nazwa": _clean_text(raw.loc[selected, "klient_nazwa"]),
        "B_odczytane": direction.loc[selected], "G_odczytane": revision.loc[selected],
        "H": hours.loc[selected].where(valid_h.loc[selected]), "I": values.loc[selected],
        "zero_I": values.loc[selected].eq(0),
        "niepoprawne_H": ~valid_h.loc[selected],
        "problem_I": checks["brak_lub_niepoprawne_I"].loc[selected],
    })
    keys = ["doba_odczytana", "oddzial_code", "klient_nazwa", "B_odczytane", "G_odczytane"]
    patterns = work.groupby(keys, dropna=False, as_index=False).agg(
        rekordy=("H", "size"), unikalne_H=("H", "nunique"),
        godziny=("H", lambda s: _hours_text(s.dropna())),
        zera_I=("zero_I", "sum"), problemy_I=("problem_I", "sum"),
        niepoprawne_H=("niepoprawne_H", "sum"),
    )
    if not work.empty:
        conflicts = work.loc[work.H.notna()].groupby([*keys, "H"], dropna=False).I.nunique(dropna=False).gt(1)
        conflicts = conflicts.groupby(level=list(range(len(keys)))).sum().rename("konflikty_I_w_tej_samej_G")
        patterns = patterns.merge(conflicts.reset_index(), on=keys, how="left")
    else:
        patterns["konflikty_I_w_tej_samej_G"] = pd.Series(dtype=int)
    patterns["konflikty_I_w_tej_samej_G"] = patterns["konflikty_I_w_tej_samej_G"].fillna(0).astype(int)
    patterns["liczba_godzin_doby"] = patterns.doba_odczytana.map(length_by_date)
    patterns["niepoprawny_klucz_serii"] = (patterns.oddzial_code.isna() | patterns.klient_nazwa.isna()
        | ~patterns.B_odczytane.isin([1, -1]) | ~patterns.G_odczytane.isin(range(5)))
    for name in ["sekwencyjne", "zegarowe_25"]:
        patterns[name] = pd.Series(index=patterns.index, dtype="string")
    for index, row in patterns.iterrows():
        observed = {int(value) for value in row.godziny.split(", ") if value}
        sequential = set(range(1, int(row.liczba_godzin_doby) + 1))
        clock = set(range(1, 26)) if row.liczba_godzin_doby == 25 else set(range(1, 25)) - {3}
        for name, expected in [("sekwencyjne", sequential), ("zegarowe_25", clock)]:
            patterns.at[index, name] = ("NIEPOPRAWNY_KLUCZ" if row.niepoprawny_klucz_serii else
                                       "NIEPOPRAWNE_H" if row.niepoprawne_H else _match(observed, expected))

    frequencies = work.groupby(["doba_odczytana", "H"], dropna=False, as_index=False).agg(
        rekordy=("I", "size"), zera_I=("zero_I", "sum"), problemy_I=("problem_I", "sum"),
        niezerowe_skonczone_I=("I", lambda s: int((np.isfinite(s) & s.ne(0)).sum())),
    )
    comparison_rows = []
    for day in changed.itertuples(index=False):
        h = pd.Series(range(1, 26))
        f = pd.Series(day.doba_odczytana, index=h.index)
        clock = trade_time_utc(f, h, timezone)
        sequential = day.doba_odczytana.tz_localize(timezone).tz_convert("UTC") + pd.to_timedelta(h - 1, unit="h")
        sequential = sequential.where(h.le(day.liczba_godzin_doby))
        table = pd.DataFrame({"doba_odczytana": f, "H": h, "sekwencyjne_UTC": sequential, "zegarowe_25_UTC": clock})
        # ISO z offsetem odróżnia dwie jesienne 02:00 także w Excelu.
        for name, times in [("sekwencyjne_lokalnie", sequential), ("zegarowe_25_lokalnie", clock)]:
            table[name] = times.map(lambda ts: "" if pd.isna(ts) else ts.tz_convert(timezone).isoformat())
        comparison_rows.append(table)
    comparison = pd.concat(comparison_rows, ignore_index=True) if comparison_rows else pd.DataFrame(columns=[
        "doba_odczytana", "H", "sekwencyjne_UTC", "zegarowe_25_UTC", "sekwencyjne_lokalnie", "zegarowe_25_lokalnie",
    ])

    lines = [
        "DIAGNOSTYKA GODZIN ENERGII — BEZ ZMIANY DANYCH I MODELU",
        f"Rekordy źródłowe: {len(raw)}. Strefa: {timezone}.",
        f"Zakres rozpoznanych F: {dates.min()} — {dates.max()}.",
        "Badany jest cały eksport, również daty sprzed start_date treningu.",
        "F/H są odczytywane tym samym parserem co w treningu; oryginały pozostają w CSV.",
        "Kontrole wejścia: " + ", ".join(f"{key}={int(mask.sum())}" for key, mask in checks.items()),
        "Wzorce są osobne dla doby/oddziału/MDD/B/G; korekty nie są sumowane ani wybierane.",
        "Zera są obserwacjami. Nie uznajemy ich automatycznie za techniczne wypełnienie.",
        "",
    ]
    for day in changed.itertuples(index=False):
        subset = patterns.loc[patterns.doba_odczytana.eq(day.doba_odczytana)]
        row_mask = dates.eq(day.doba_odczytana)
        lines += [
            f"{day.doba_odczytana.date()} {day.typ_doby}: {day.liczba_godzin_doby} rzeczywiste godziny; rekordy={int(row_mask.sum())}.",
            f"  Zaobserwowane H (łącznie, nie dowód kompletności serii): {_hours_text(hours.loc[row_mask & valid_h]) or 'brak'}.",
            f"  Wzorce serii: sekwencyjne={subset.sekwencyjne.value_counts().to_dict()}, zegarowe_25={subset.zegarowe_25.value_counts().to_dict()}.",
        ]
        for hour in ([3, 24] if day.liczba_godzin_doby == 23 else [3, 4, 25]):
            observations = values.loc[row_mask & hours.eq(hour)]
            lines.append(f"  H={hour}: rekordy={len(observations)}, zera={int(observations.eq(0).sum())}, "
                         f"niezerowe_skończone={int((np.isfinite(observations) & observations.ne(0)).sum())}, "
                         f"brak_lub_niepoprawne_I={int(checks['brak_lub_niepoprawne_I'].loc[observations.index].sum())}.")
    spring = changed.loc[changed.liczba_godzin_doby.eq(23), "doba_odczytana"]
    if not dates.isin(spring).any():
        lines.append("Brak rekordów z wiosennej zmiany czasu: nie można porównać rozróżniających wzorców 1–23 oraz 1,2,4…24.")
    spring_patterns = patterns.loc[patterns.doba_odczytana.isin(spring)]
    usable = spring_patterns.loc[spring_patterns.problemy_I.eq(0) & spring_patterns.konflikty_I_w_tej_samej_G.eq(0)]
    sequential_count = int(usable.sekwencyjne.eq("PELNY_WZORZEC").sum())
    clock_count = int(usable.zegarowe_25.eq("PELNY_WZORZEC").sum())
    if sequential_count and clock_count:
        verdict = "wiosną występują OBA pełne wzorce. Wymagają wyjaśnienia osobno dla źródeł/serii."
    elif sequential_count:
        verdict = "są wiosenne pełne zestawy 1–23, typowe dla numeracji kolejnych rzeczywistych godzin."
    elif clock_count:
        verdict = "są wiosenne pełne zestawy 1,2,4…24, typowe dla numeracji zegarowej z pominięciem 3."
    else:
        verdict = "brak rozstrzygającego pełnego wiosennego wzorca bez problemów I i konfliktów tej samej korekty."
    lines += ["", "WYNIK DIAGNOSTYKI: " + verdict,
              "To wskazówka o etykietach; nie potwierdza automatycznie znaczenia jesiennej H25 we wszystkich seriach."]
    lines += [
        "",
        "INTERPRETACJA: sekwencyjne = kolejne rzeczywiste godziny od lokalnej północy.",
        "zegarowe_25 = H1 to 00:00, H3 to pierwsza 02:00, H25 to druga 02:00 jesienią; H3 nie istnieje wiosną.",
        "PELNY_WZORZEC potwierdza zestaw etykiet, nie znaczenie fizycznego czasu ani kompletność pomiarów.",
        "Jesienne etykiety 1–25 pasują do OBU wariantów. Ich kolejność w pliku nie dowodzi kolejności czasu.",
        "Sama energia I nie potwierdza znaczenia H25. Bez rozstrzygających danych potrzebny jest opis eksportu lub niezależny znacznik czasu.",
        "Raport nie wybiera numeracji i nie przesuwa ani nie odrzuca obserwacji. Nie uruchamia SQL, treningu ani fit().",
        "Do omówienia wyniku wystarczy ten plik podsumowania; szczegółowe CSV zawierają lokalne dane MDD.",
    ]
    return HourDiagnostics("\n".join(lines) + "\n", {
        "problemy_wejscia.csv": problems,
        "rekordy_zmiany_czasu.csv": details,
        "wzorce_serii.csv": patterns,
        "liczniki_godzin.csv": frequencies,
        "porownanie_utc.csv": comparison,
    })


def write_hour_diagnostics(energy: str | Path, output_dir: str | Path, *, timezone: str = "Europe/Warsaw",
                           sheet_regex: str = PipelineConfig().sheet_regex) -> Path:
    source, directory = Path(energy).expanduser().resolve(), Path(output_dir).expanduser().resolve()
    print(f"Odczyt całego eksportu: {source}", flush=True)
    raw = read_energy_file(source, sheet_regex=sheet_regex)
    print(f"Odczytano {len(raw)} rekordów. Sprawdzanie dni zmiany czasu…", flush=True)
    result = diagnose_energy_hours(raw, timezone)
    # Nie wolno nadpisać eksportu, nawet gdy ma taką samą nazwę jak raport.
    targets = [directory / name for name in [*result.tables, "podsumowanie_godzin.txt"]]
    if any(path.resolve() == source or (path.exists() and path.samefile(source)) for path in targets):
        raise ValueError("Folder raportu spowodowałby nadpisanie pliku energii. Wybierz osobny folder.")
    directory.mkdir(parents=True, exist_ok=True)
    for name, table in result.tables.items():
        table.to_csv(directory / name, sep=";", decimal=",", index=False, encoding="utf-8-sig")
    report = directory / "podsumowanie_godzin.txt"
    report.write_text(result.summary, encoding="utf-8-sig")
    print(result.summary, flush=True)
    print(f"Podsumowanie do omówienia: {report}", flush=True)
    return report


def diagnostic_launch(energy: str = "", output_dir: str = "") -> int:
    """Strzałka VS Code: przejmij ścieżki z lokalnego launchera treningu."""
    from .branch_launcher import ROOT, _path
    import uruchom_trening_bilansow as training

    source = str(energy).strip() or str(training.SCIEZKA_ENERGII).strip()
    model_dir = str(training.FOLDER_MODELU).strip() or "model_bilansu"
    directory = str(output_dir).strip() or str(Path(_path(model_dir)) / "diagnostyka_godzin")
    if not source:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            source = filedialog.askopenfilename(parent=root, title="Wybierz energię do diagnostyki godzin",
                initialdir=str(ROOT), filetypes=[("Excel i CSV", "*.csv *.xlsx *.xlsm"), ("Wszystkie pliki", "*.*")])
        finally:
            root.destroy()
        if not source:
            return 0
    write_hour_diagnostics(_path(source), _path(directory), sheet_regex=training.PARAMETRY.get("sheet_regex", PipelineConfig().sheet_regex))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnostyka godzin energii bez SQL i treningu; obejmuje cały eksport.")
    parser.add_argument("--energy", required=True, help="CSV/XLSX/XLSM energii A:I")
    parser.add_argument("--output-dir", default="model_bilansu/diagnostyka_godzin")
    parser.add_argument("--timezone", default="Europe/Warsaw")
    parser.add_argument("--sheet-regex", default=PipelineConfig().sheet_regex)
    args = parser.parse_args(argv)
    write_hour_diagnostics(args.energy, args.output_dir, timezone=args.timezone, sheet_regex=args.sheet_regex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
