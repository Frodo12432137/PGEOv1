from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from .branch_data import load_branch_energy, prepare_weather_vintages, utc_timestamp, future_requests, load_prediction_measurements
from .branch_database import DEFAULT_BRANCH_SQL, query_branch_weather_sql
from .branch_model import TrainingSettings, train_branch_models, save_bundle, load_bundle, predict_branches
from .config import PipelineConfig, DEFAULT_MAPPING_PATH
from .database import SqlServerSettings, DEFAULT_SQL_SERVER, DEFAULT_SQL_DATABASE, DEFAULT_SQL_DRIVER
from .io import read_weather_file
from .portfolio import analyze_portfolio


def write_csv(frame: pd.DataFrame, path: Path):
    frame.to_csv(path, sep=";", decimal=",", index=False, encoding="utf-8-sig")


def _weather_arguments(parser):
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--weather", help="CSV/XLSX z całym archiwum wydań prognoz pogody.")
    group.add_argument("--weather-sql", help="Oddzielny plik SQL uruchamiany przez pyodbc.")
    parser.add_argument("--sql-server", default=DEFAULT_SQL_SERVER)
    parser.add_argument("--sql-database", default=DEFAULT_SQL_DATABASE)
    parser.add_argument("--sql-driver", default=DEFAULT_SQL_DRIVER)
    parser.add_argument("--sql-owner", help="Opcjonalny filtr właściciela. Brak oznacza bez filtra.")
    parser.add_argument("--sql-weather-type", help="Opcjonalny filtr typu, np. klient zagregowany.")
    parser.add_argument("--sql-query-timeout", type=int, default=0)
    parser.add_argument("--sql-encrypt", action="store_true")
    parser.add_argument("--sql-trust-server-certificate", action="store_true")


def _weather(args, config, start, end, asof):
    if args.weather:
        raw = read_weather_file(args.weather)
    else:
        settings = SqlServerSettings(server=args.sql_server, database=args.sql_database,
                                     driver=args.sql_driver, encrypt=args.sql_encrypt,
                                     trust_server_certificate=args.sql_trust_server_certificate)
        print(f"Pobieram pogodę z SQL: {start} – {end}. Czas dostępności do {asof}.")
        raw = query_branch_weather_sql(args.weather_sql, settings, start, end, asof,
                                       owner=args.sql_owner, weather_type=args.sql_weather_type,
                                       query_timeout=args.sql_query_timeout)
    return prepare_weather_vintages(raw, config)


def train_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Nauka modeli pobrania, oddania i bilansu oddziałów PGE.")
    parser.add_argument("--energy", required=True)
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING_PATH))
    parser.add_argument("--sheet-regex", default=PipelineConfig().sheet_regex)
    parser.add_argument("--start-date", default="2024-07-01")
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--origin-step-hours", type=int, default=12)
    parser.add_argument("--validation-days", type=int, default=14)
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--label-delay-hours", type=int, default=72)
    parser.add_argument("--max-iter", type=int, default=180)
    parser.add_argument("--min-train-rows", type=int, default=200)
    parser.add_argument("--max-train-rows", type=int, default=400_000)
    parser.add_argument("--models", choices=["auto", "hist_gradient_boosting", "catboost"], default="hist_gradient_boosting",
                        help="auto porównuje HistGradientBoosting i zainstalowany CatBoost; wybór tylko na walidacji.")
    parser.add_argument("--learning-rate", type=float, default=0.06)
    parser.add_argument("--max-leaf-nodes", type=int, default=15)
    parser.add_argument("--min-samples-leaf", type=int, default=20)
    parser.add_argument("--l2-regularization", type=float, default=2.0)
    parser.add_argument("--catboost-depth", type=int, default=6)
    parser.add_argument("--catboost-l2-leaf-reg", type=float, default=5.0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--min-evaluation-targets", type=int, default=24,
                        help="Minimalna liczba unikalnych godzin każdego oddziału/kierunku osobno w walidacji i teście.")
    parser.add_argument("--weather-variants", nargs="+", default=list(TrainingSettings().weather_variants),
                        choices=list(TrainingSettings().weather_variants), help="Warianty pogody do porównania na walidacji.")
    parser.add_argument("--tune", action="store_true", help="Porównaj trzy zestawy parametrów każdej rodziny modelu.")
    parser.add_argument("--skip-importance", action="store_true")
    parser.add_argument("--importance-rows", type=int, default=1500,
                        help="Maksymalna próbka testu do ważności grup pogody; 0 wyłącza.")
    parser.add_argument("--portfolio-grace-days", type=int, default=14,
                        help="Po ilu dniach braku pomiarów oznaczyć MDD do weryfikacji (nie koniec umowy).")
    _weather_arguments(parser)
    args = parser.parse_args(argv)
    config = PipelineConfig(mapping_path=Path(args.mapping), sheet_regex=args.sheet_regex)
    settings = TrainingSettings(horizon_hours=args.horizon_hours, origin_step_hours=args.origin_step_hours,
                                validation_days=args.validation_days, test_days=args.test_days,
                                label_delay_hours=args.label_delay_hours, max_iter=args.max_iter,
                                min_train_rows=args.min_train_rows, max_train_rows=args.max_train_rows,
                                tune=args.tune, importance_rows=0 if args.skip_importance else args.importance_rows,
                                model_backend=args.models, learning_rate=args.learning_rate,
                                max_leaf_nodes=args.max_leaf_nodes, min_samples_leaf=args.min_samples_leaf,
                                l2_regularization=args.l2_regularization, catboost_depth=args.catboost_depth,
                                catboost_l2_leaf_reg=args.catboost_l2_leaf_reg, threads=args.threads,
                                min_evaluation_targets=args.min_evaluation_targets,
                                weather_variants=tuple(args.weather_variants), random_state=args.random_state)
    settings.validate()
    energy, quality, mapping, measurements = load_branch_energy(args.energy, config, return_measurements=True)
    energy = energy.loc[energy["doba_handlowa"].ge(pd.Timestamp(args.start_date))].copy()
    measurements = measurements.loc[measurements["doba_handlowa"].ge(pd.Timestamp(args.start_date))].copy()
    if energy.empty:
        raise ValueError("Brak pomiarów po dacie startowej.")
    origin = pd.Timestamp.now(tz="UTC")
    if energy["target_time_utc"].max() > origin:
        raise ValueError("Eksport ma realizacje energii z przyszłości. Sprawdź daty danych.")
    portfolio_reports = analyze_portfolio(measurements, args.portfolio_grace_days)
    start = energy["doba_handlowa"].min()
    end = energy["doba_handlowa"].max() + pd.Timedelta(days=1)
    weather = _weather(args, config, start, end, origin)
    print("Uczę modele. Porównuję pogodę na walidacji, następnie oceniam na osobnym teście.")
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=settings.threads):
        bundle, reports = train_branch_models(energy, weather, settings)
    bundle["portfolio_reports"] = portfolio_reports
    bundle["portfolio_grace_days"] = args.portfolio_grace_days
    bundle["input_schema"] = {"sheet_regex": config.sheet_regex, "start_date": args.start_date}
    directory = Path(args.artifacts_dir)
    directory.mkdir(parents=True, exist_ok=True)
    save_bundle(bundle, directory)
    write_csv(quality, directory / "kontrola_energii.csv")
    write_csv(mapping, directory / "mapowanie_zrodlowe.csv")
    write_csv(energy, directory / "historia_oddzialow.csv")
    for name, frame in reports.items():
        write_csv(frame, directory / f"{name}.csv")
    for name, frame in portfolio_reports.items():
        write_csv(frame, directory / f"{name}.csv")
    from .branch_diagnostics import write_training_workbook, print_training_summary
    write_training_workbook(bundle, {**reports, **portfolio_reports, "kontrola_energii": quality},
                            directory / "raport_treningu.xlsx")
    for direction, choice in bundle["models"].items():
        print(f"{direction}: wybrany wariant {choice['wariant']}")
    print_training_summary(reports)
    print(f"Gotowe. Model i raporty: {directory.resolve()}")
    return 0


def predict_main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prognoza 72 h z zapisanych modeli; bez ponownego uczenia.")
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--as-of", help="Chwila uruchomienia CET/CEST albo ISO z offsetem. Domyślnie teraz.")
    parser.add_argument("--horizon-hours", type=int, help="Domyślnie horyzont zapisany przy nauce, 72 h.")
    parser.add_argument("--energy", help="Opcjonalny aktualny eksport energii do kontroli zmian MDD. Nie uruchamia treningu.")
    _weather_arguments(parser)
    args = parser.parse_args(argv)
    bundle = load_bundle(args.artifacts_dir)
    config = PipelineConfig(timezone=bundle["settings"]["timezone"])
    asof = utc_timestamp(args.as_of if args.as_of else pd.Timestamp.now(tz="UTC"), config.timezone)
    portfolio_reports = None
    if args.energy:
        measurements = load_prediction_measurements(args.energy, bundle)
        if measurements["target_time_utc"].max() > asof:
            raise ValueError("Eksport do kontroli portfela ma pomiary późniejsze niż chwila prognozy.")
        portfolio_reports = analyze_portfolio(measurements, bundle.get("portfolio_grace_days", 14))
    hours = args.horizon_hours if args.horizon_hours is not None else bundle["settings"]["horizon_hours"]
    if hours > bundle["settings"]["horizon_hours"]:
        raise ValueError("Horyzont jest większy niż zakres treningu.")
    requests = future_requests(bundle["branches"], asof, hours, config.timezone)
    start = requests["target_time_utc"].min().tz_convert(config.timezone).tz_localize(None)
    end = (requests["target_time_utc"].max() + pd.Timedelta(hours=1)).tz_convert(config.timezone).tz_localize(None)
    weather = _weather(args, config, start, end, asof)
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=4):
        balance, details = predict_branches(bundle, weather, asof, hours, portfolio_reports=portfolio_reports)
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    forecast_path = directory / f"prognoza_bilansu_{hours}h.csv"
    write_csv(balance, forecast_path)
    write_csv(details, directory / "szczegoly_prognozy.csv")
    effective_reports = portfolio_reports if portfolio_reports is not None else bundle.get("portfolio_reports")
    if effective_reports:
        for name, frame in effective_reports.items():
            write_csv(frame, directory / f"{name}.csv")
    missing = int(balance["bilans"].isna().sum())
    unverified = int(balance["status_portfela"].ne("BEZ_DLUGICH_BRAKOW_W_POMIARACH").sum())
    print(f"Prognoza: {forecast_path.resolve()}. Brakujące bilanse: {missing}.")
    print(f"Wiersze z portfelem wymagającym weryfikacji: {unverified}. Sprawdź kolumnę status_portfela.")
    partial = int(balance["status"].eq("PROGNOZA_NIEPELNA_POGODA").sum())
    if partial:
        print(f"UWAGA: {partial} bilansów opiera się na niepełnej pogodzie. Szczegóły: brakujace_cechy_pogody w szczegoly_prognozy.csv.")
    return 2 if missing or partial else 0
