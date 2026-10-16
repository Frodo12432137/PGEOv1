"""Backtest zamrożonego modelu: produkcyjny trening i produkcyjna predykcja."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import re
import shutil
import sys
import time
import traceback
from uuid import uuid4

import numpy as np
import pandas as pd

from .backtest_runtime import atomic_json
from .branch_data import load_branch_energy
from .branch_model import (balance_table, load_bundle, predict_branches, save_bundle,
                           settings_from_options, train_branch_models)
from .config import PipelineConfig


def date_boundary(value: str, timezone: str) -> pd.Timestamp:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError(f"Data {value!r}: użyj RRRR-MM-DD, np. 2025-07-01.")
    return pd.Timestamp(value).tz_localize(timezone).tz_convert("UTC")


def time_scope(configuration: dict, settings):
    zone = settings.timezone
    start = date_boundary(configuration["train_from"], zone)
    # Dodawanie do daty lokalnej, nie 24 h do daty ze strefą (zmiany czasu).
    date_boundary(configuration["train_to"], zone)
    date_boundary(configuration["backtest_to"], zone)
    end = date_boundary(str(pd.Timestamp(configuration["train_to"]).date() + pd.Timedelta(days=1)), zone)
    test_start = date_boundary(configuration["backtest_from"], zone)
    test_end = date_boundary(str(pd.Timestamp(configuration["backtest_to"]).date() + pd.Timedelta(days=1)), zone)
    if start >= end or test_start >= test_end:
        raise ValueError("Data OD nie może być późniejsza od DO.")
    origins = pd.date_range(test_start.floor("D"), test_end, freq=f"{settings.origin_step_hours}h")
    origins = origins[(origins >= test_start) & (origins < test_end)]
    if origins.empty:
        raise ValueError("Brak uruchomień w podanym zakresie. Zmniejsz origin_step_hours.")
    if end + pd.Timedelta(hours=settings.label_delay_hours) > origins[0]:
        raise ValueError("Trening nachodzi na backtest lub embargo. Zakończ trening wcześniej: po końcu doby TRENING_DO "
                         f"wymagamy {settings.label_delay_hours} h odstępu przed pierwszą prognozą {origins[0]}.")
    return start, end, origins


def dst_target_mask(times: pd.Series, timezone: str) -> pd.Series:
    dates = times.dt.tz_convert(timezone).dt.tz_localize(None).dt.normalize()
    unique = pd.DatetimeIndex(dates.drop_duplicates())
    durations = ((unique + pd.Timedelta(days=1)).tz_localize(timezone).tz_convert("UTC")
                 - unique.tz_localize(timezone).tz_convert("UTC")) / pd.Timedelta(hours=1)
    return dates.isin(unique[durations != 24])


def score_origin(bundle: dict, weather: pd.DataFrame, energy: pd.DataFrame, origin):
    """Prognozy są dokładnie z predict_branches(); realizacje dołączamy dopiero potem."""
    horizon = bundle["settings"]["horizon_hours"]
    first = origin.floor("h") + pd.Timedelta(hours=1)
    last = first + pd.Timedelta(hours=horizon - 1)
    wx = weather.loc[weather.weather_valid_time_utc.between(first, last)]
    production_balance, details = predict_branches(bundle, wx, origin)
    keys = ["oddzial_code", "kierunek_energii_norm", "target_time_utc"]
    optional = [name for name in ["liczba_odrzuconych_pomiarow", "status_jakosci_energii"] if name in energy]
    details = details.merge(energy[[*keys, "wartosc_rzeczywista", *optional]], on=keys, how="left", validate="many_to_one")
    if "liczba_odrzuconych_pomiarow" not in details:
        details["liczba_odrzuconych_pomiarow"] = 0
    details["liczba_odrzuconych_pomiarow"] = details.liczba_odrzuconych_pomiarow.fillna(0).astype(int)
    exclude = bundle.get("input_schema", {}).get("exclude_dst_days", True)
    details["cel_wylaczony_DST"] = dst_target_mask(details.target_time_utc, bundle["settings"]["timezone"]) if exclude else False
    details["status_oceny"] = np.select(
        [details.cel_wylaczony_DST, details.liczba_odrzuconych_pomiarow.gt(0), details.wartosc_rzeczywista.isna(), details.wartosc_przewidywana.isna()],
        ["POMINIETA_DOBA_ZMIANY_CZASU", "POMIAR_WYKLUCZONY_FILTREM", "BRAK_REALIZACJI", "BRAK_PROGNOZY"], default="OCENIONO")
    balance = balance_table(details, ["wartosc_rzeczywista", "wartosc_przewidywana", "wartosc_bazowa"])
    merge_keys = ["oddzial_code", "target_time_utc", "forecast_origin_utc"]
    balance = balance.merge(production_balance[[*merge_keys, "status", "status_portfela"]],
                            on=merge_keys, how="left", validate="one_to_one")
    counts = details.groupby(merge_keys, as_index=False).liczba_odrzuconych_pomiarow.sum()
    balance = balance.merge(counts, on=merge_keys, how="left", validate="one_to_one")
    balance["cel_wylaczony_DST"] = dst_target_mask(balance.target_time_utc, bundle["settings"]["timezone"]) if exclude else False
    balance["status_oceny"] = np.select(
        [balance.cel_wylaczony_DST, balance.liczba_odrzuconych_pomiarow.gt(0), balance.bilans_wartosc_rzeczywista.isna(), balance.bilans_wartosc_przewidywana.isna()],
        ["POMINIETA_DOBA_ZMIANY_CZASU", "POMIAR_WYKLUCZONY_FILTREM", "BRAK_REALIZACJI", "BRAK_PROGNOZY"], default="OCENIONO")
    return balance, details


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_signature(configuration: dict) -> str:
    """Pełna zawartość wejść/kodu/ustawień i wersje, a nie same nazwy plików."""
    sources = {name: file_digest(Path(configuration[name])) for name in ("energy", "weather_file", "mapping")}
    code = {path.name: file_digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))}
    libraries = {"python": platform.python_version()}
    for name in ("numpy", "pandas", "scikit-learn", "catboost", "lightgbm", "xgboost", "xgboost-cpu", "joblib", "openpyxl"):
        try:
            libraries[name] = version(name)
        except PackageNotFoundError:
            libraries[name] = None
    values = {key: value for key, value in configuration.items() if key not in {"run_dir", "cache_root", "runtime"}}
    return hashlib.sha256(json.dumps([values, sources, code, libraries], sort_keys=True).encode()).hexdigest()


def dump_atomic(value, path: Path):
    import joblib
    temporary = path.with_suffix(path.suffix + ".tmp")
    joblib.dump(value, temporary)
    temporary.replace(path)


def run_backtest(configuration: dict) -> int:
    import joblib
    from threadpoolctl import threadpool_limits
    from .branch_cli import _weather, _weather_arguments, write_csv
    from .branch_launcher import _options
    from .branch_diagnostics import write_training_workbook
    from .backtest_report import write_backtest_reports
    from .portfolio import analyze_portfolio
    from .energy_filter import filter_settings_from_options, prepare_training_energy, apply_energy_filter
    from .branch_data import sum_branch_measurements

    run = Path(configuration["run_dir"])
    run.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    status = {"kompletny": False, "gotowe_prognozy": 0}

    def update(stage, **values):
        status.update(etap=stage, czas_s=round(time.monotonic() - started, 2), **values)
        atomic_json(run / "status_backtestu.json", status)
        print(f"[{stage}] {values}", flush=True)

    parameters = configuration["parameters"]
    settings = settings_from_options(parameters)
    filter_settings = filter_settings_from_options(parameters)
    train_start, train_end, origins = time_scope(configuration, settings)
    runtime = configuration.get("runtime", {})
    report_every = int(runtime.get("report_every_origins", 10))
    max_html_origins = int(runtime.get("max_html_origins", 200))
    if min(report_every, max_html_origins) < 1:
        raise ValueError("report_every_origins i max_html_origins muszą być dodatnie.")
    status["wszystkie_prognozy"] = len(origins)
    update("ODCZYT_ENERGII")
    config = PipelineConfig(mapping_path=Path(configuration["mapping"]), timezone=settings.timezone,
                            sheet_regex=parameters.get("sheet_regex", PipelineConfig().sheet_regex))
    last_target = origins[-1].floor("h") + pd.Timedelta(hours=settings.horizon_hours)
    energy, quality, mapping, measurements, excluded = load_branch_energy(
        configuration["energy"], config, return_measurements=True,
        start_date=configuration["train_from"], end_date=str(last_target.tz_convert(settings.timezone).date()),
        exclude_dst_days=not parameters.get("include_dst_days", False), return_excluded_days=True,
        exclude_negative_energy=filter_settings.exclude_negative_energy)
    training = energy.loc[energy.target_time_utc.ge(train_start) & energy.target_time_utc.lt(train_end)].copy()
    historical_measurements = measurements.loc[measurements.target_time_utc.ge(train_start)
                                               & measurements.target_time_utc.lt(train_end)].copy()
    actual_test = energy.loc[energy.target_time_utc.gt(origins[0]) & energy.target_time_utc.le(last_target)]
    for label, frame in [("treningu", training), ("backtestu", actual_test)]:
        if frame.empty or not frame.wartosc_rzeczywista.notna().any():
            raise ValueError(f"Brak poprawnych realizacji energii w zakresie {label}. Ustaw daty zgodne ze swoim plikiem.")
    write_csv(quality, run / "kontrola_energii.csv")
    write_csv(excluded, run / "pominiete_dni_zmiany_czasu.csv")
    update("SPRAWDZANIE_CACHE")
    signature = run_signature(configuration)
    cache = Path(configuration["cache_root"]) / signature
    if not runtime.get("resume", True):
        cache = cache / uuid4().hex
    cache.mkdir(parents=True, exist_ok=True)
    weather_cache = cache / "pogoda.joblib"
    if weather_cache.is_file():
        weather = joblib.load(weather_cache)
        update("POGODA_Z_ZAPISANEGO_ARCHIWUM", cache=str(cache))
    else:
        parser = argparse.ArgumentParser()
        _weather_arguments(parser)
        weather_file = configuration["weather_file"]
        option = "--weather-sql" if Path(weather_file).suffix.lower() == ".sql" else "--weather"
        args = parser.parse_args([option, weather_file, *_options(configuration.get("weather_options"))])
        update("ODCZYT_POGODY")
        weather = _weather(args, config, train_start, last_target + pd.Timedelta(hours=1),
                           origins[-1], diagnostics_dir=run)
        dump_atomic(weather, weather_cache)
    weather_hash = file_digest(weather_cache)
    if weather.empty:
        raise ValueError("Puste archiwum prognoz pogody.")
    from .branch_cli import write_weather_time_reports
    time_reports = write_weather_time_reports(weather, run)
    trials_file = cache / "proby_strojenia.json"
    trials = {}
    if trials_file.is_file():
        stored = json.loads(trials_file.read_text(encoding="utf-8"))
        if stored["pogoda_sha256"] != weather_hash:
            raise ValueError("Archiwum pogody w cache zmieniło się. Ustaw resume=False.")
        trials = stored["proby"]

    def progress(event):
        stage = event.pop("etap")
        update(stage, **event)
        if stage == "PROBA_GOTOWA":
            atomic_json(trials_file, {"pogoda_sha256": weather_hash, "proby": trials})
            write_csv(pd.DataFrame(trials.values()), run / "postep_strojenia.csv")

    ready_file = cache / "gotowy_model.json"
    if ready_file.is_file():
        marker = json.loads(ready_file.read_text(encoding="utf-8"))
        if marker["pogoda_sha256"] != weather_hash:
            raise ValueError("Gotowy model ma inne archiwum pogody. Ustaw resume=False.")
        bundle = load_bundle(cache / "trening")
        training_reports = joblib.load(cache / "raporty_treningu.joblib")
        update("GOTOWY_MODEL_Z_CACHE")
    else:
        training, historical_measurements, filter_state, filter_reports, split_cutoff = prepare_training_energy(
            training, historical_measurements, weather, settings, filter_settings)
        with threadpool_limits(limits=settings.threads):
            bundle, training_reports = train_branch_models(training, weather, settings,
                                                            trial_cache=trials, progress=progress, split_cutoff=split_cutoff)
        bundle["energy_filter"] = filter_state
        training_reports.update(filter_reports)
        grace = parameters.get("portfolio_grace_days", 14)
        portfolio = analyze_portfolio(historical_measurements, grace, ignored_dates=excluded.doba_handlowa)
        bundle.update(portfolio_reports=portfolio, portfolio_grace_days=grace,
                      input_schema={"sheet_regex": config.sheet_regex, "start_date": configuration["train_from"],
                                    "exclude_dst_days": not parameters.get("include_dst_days", False),
                                    "exclude_negative_energy": filter_settings.exclude_negative_energy},
                      backtest_training_scope={"od": configuration["train_from"], "do": configuration["train_to"]})
        training_reports.update(portfolio)
        training_reports["pominiete_dni_zmiany_czasu"] = excluded.loc[excluded.doba_handlowa.lt(train_end.tz_convert(settings.timezone).tz_localize(None))]
        save_bundle(bundle, cache / "trening")
        dump_atomic(training_reports, cache / "raporty_treningu.joblib")
        atomic_json(ready_file, {"pogoda_sha256": weather_hash})
    training_reports.update(time_reports)
    measurements, all_filter_reports = apply_energy_filter(measurements, bundle["energy_filter"], settings.timezone)
    energy = sum_branch_measurements(measurements, settings.timezone)
    all_filter_reports["godziny_wykluczone_filtrem"] = energy.loc[energy.liczba_odrzuconych_pomiarow.gt(0)].copy()
    for name, frame in all_filter_reports.items():
        write_csv(frame, run / f"{name}.csv")
    destination = run / "trening"
    shutil.copytree(cache / "trening", destination)
    for name, frame in training_reports.items():
        write_csv(frame, destination / f"{name}.csv")
    write_csv(mapping, destination / "mapowanie_zrodlowe.csv")
    write_training_workbook(bundle, training_reports, destination / "raport_treningu.xlsx")
    metadata = {
        "trening_od": configuration["train_from"], "trening_do": configuration["train_to"],
        "faktyczny_trening_od_UTC": str(training.target_time_utc.min()),
        "faktyczny_trening_do_UTC": bundle["training_target_max_utc"],
        "backtest_uruchomienia_od": configuration["backtest_from"], "backtest_uruchomienia_do": configuration["backtest_to"],
        "pierwszy_origin_UTC": str(origins[0]), "ostatni_origin_UTC": str(origins[-1]),
        "ostatni_cel_UTC": str(last_target), "sygnatura": signature,
        "pogoda_cache_sha256": weather_hash, "archiwum_pogody": str(weather_cache),
        "horyzont_h": settings.horizon_hours, "krok_origin_h": settings.origin_step_hours,
        "planowane_prognozy": len(origins), "model_zamrozony": True,
    }
    atomic_json(run / "zakres_backtestu.json", metadata)
    balances, details = [], []
    checkpoints = run / "przebiegi"
    checkpoints.mkdir()
    with threadpool_limits(limits=settings.threads):
        for number, origin in enumerate(origins, 1):
            update("BACKTEST", origin=str(origin), gotowe_prognozy=number - 1)
            balance, detail = score_origin(bundle, weather, energy, origin)
            write_csv(balance, checkpoints / f"bilans_{origin:%Y%m%d_%H%M}.csv")
            balances.append(balance)
            details.append(detail)
            if number == 1 or number % report_every == 0 or number == len(origins):
                update("RAPORT", gotowe_prognozy=number)
                write_backtest_reports(bundle, pd.concat(details, ignore_index=True), pd.concat(balances, ignore_index=True),
                                       run, {**metadata, "gotowe_prognozy": number, "kompletny": number == len(origins)},
                                       {**training_reports, **all_filter_reports}, excluded, max_html_origins)
    combined = pd.concat(balances, ignore_index=True)
    eligible = combined.loc[~combined.cel_wylaczony_DST]
    assessed = eligible.status_oceny.eq("OCENIONO")
    incomplete = not assessed.all() or eligible.status.eq("PROGNOZA_NIEPELNA_POGODA").any()
    update("GOTOWE_Z_BRAKAMI" if incomplete else "GOTOWE", kompletny=True, gotowe_prognozy=len(origins),
           ocenione_bilanse=int(assessed.sum()), brakujace_bilanse=int((~assessed).sum()))
    print(f"Gotowe: {run / 'raport_backtestu.html'}\nExcel: {run / 'raport_backtestu.xlsx'}", flush=True)
    return 2 if incomplete else 0


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, value):
        self.log.write(value)
        self.log.flush()
        try:
            self.console.write(value)
        except UnicodeEncodeError:
            self.console.write(value.encode(self.console.encoding or "utf-8", errors="replace").decode(self.console.encoding or "utf-8"))

    def flush(self):
        self.console.flush()
        self.log.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="JSON zapisany przez uruchom_backtest_bilansow.py")
    args = parser.parse_args(argv)
    configuration = json.loads(Path(args.config).read_text(encoding="utf-8"))
    run = Path(configuration["run_dir"])
    run.mkdir(parents=True, exist_ok=True)
    with (run / "backtest.log").open("a", encoding="utf-8") as log:
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = Tee(stdout, log), Tee(stderr, log)
        try:
            return run_backtest(configuration)
        except Exception:
            error = traceback.format_exc()
            (run / "blad_backtestu.txt").write_text(error, encoding="utf-8")
            path = run / "status_backtestu.json"
            status = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            status.update(etap="BLAD", kompletny=False, komunikat=error.splitlines()[-1])
            atomic_json(path, status)
            print(error, file=sys.stderr)
            return 1
        finally:
            sys.stdout, sys.stderr = stdout, stderr


if __name__ == "__main__":
    raise SystemExit(main())
