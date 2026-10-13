"""Uczenie zapisanych modeli oddziałów i szybka predykcja bez fit()."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import platform

import numpy as np
import pandas as pd

from .branch_data import future_requests, historical_requests, join_weather_asof, utc_timestamp
from .config import WEATHER_FEATURES
from .features import _polish_holidays


WEATHER_GROUPS = {
    "temperatura": ["temperatura", "grzanie", "chlodzenie"],
    "slonce": ["promieniowanie_calkowite", "promieniowanie_bezposrednie"],
    "chmury": ["zachmurzenie"],
    "wiatr": ["predkosc_wiatru", "wiatr_sin", "wiatr_cos"],
    "opad": ["opad_konwekcyjny"],
    "snieg": ["warstwa_sniegu", "albedo"],
    "widocznosc": ["widocznosc"],
}
VARIANTS = {
    "kalendarz": [],
    "temperatura_slonce": ["temperatura", "slonce"],
    "pogoda_podstawowa": ["temperatura", "slonce", "chmury", "wiatr"],
    "pogoda_pelna": list(WEATHER_GROUPS),
}
CALENDAR_COLUMNS = ["godzina", "dzien_tygodnia", "miesiac", "weekend", "swieto_PL",
                    "godzina_sin", "godzina_cos", "rok_sin", "rok_cos", "horyzont_h"]
DIRECTIONS = ("POBRANIE", "ODDANIE")


@dataclass(frozen=True)
class TrainingSettings:
    horizon_hours: int = 72
    origin_step_hours: int = 12
    validation_days: int = 14
    test_days: int = 14
    label_delay_hours: int = 72
    min_train_rows: int = 200
    max_train_rows: int = 400_000
    max_iter: int = 180
    tune: bool = False
    importance_rows: int = 1500
    timezone: str = "Europe/Warsaw"
    random_state: int = 42

    def validate(self):
        if not 1 <= self.horizon_hours <= 168:
            raise ValueError("Horyzont musi należeć do zakresu 1–168 godzin.")
        if min(self.origin_step_hours, self.validation_days, self.test_days, self.min_train_rows, self.max_iter) < 1:
            raise ValueError("Odstępy czasu, rozmiary okien i liczba iteracji muszą być dodatnie.")
        if self.max_train_rows < self.min_train_rows or self.label_delay_hours < 0:
            raise ValueError("Niepoprawny limit danych lub opóźnienie pomiarów.")


def feature_frame(frame: pd.DataFrame, branch_codes: list[str], timezone_name: str) -> pd.DataFrame:
    ts = frame["target_time_utc"].dt.tz_convert(timezone_name)
    out = pd.DataFrame(index=frame.index)
    out["godzina"] = ts.dt.hour.astype(float)
    out["dzien_tygodnia"] = ts.dt.dayofweek.astype(float)
    out["miesiac"] = ts.dt.month.astype(float)
    out["weekend"] = ts.dt.dayofweek.ge(5).astype(float)
    holidays = set().union(*[_polish_holidays(int(y)) for y in ts.dt.year.unique()])
    out["swieto_PL"] = ts.dt.date.isin(holidays).astype(float)
    out["godzina_sin"] = np.sin(2 * np.pi * out["godzina"] / 24)
    out["godzina_cos"] = np.cos(2 * np.pi * out["godzina"] / 24)
    out["rok_sin"] = np.sin(2 * np.pi * ts.dt.dayofyear / 365.25)
    out["rok_cos"] = np.cos(2 * np.pi * ts.dt.dayofyear / 365.25)
    out["horyzont_h"] = frame["horyzont_h"]
    for code in branch_codes:
        out[f"oddzial_{code}"] = frame["oddzial_code"].eq(code).astype(float)
    for col in WEATHER_FEATURES:
        out[col] = pd.to_numeric(frame[col], errors="coerce")
    out["grzanie"] = (18 - out["temperatura"]).clip(lower=0)
    out["chlodzenie"] = (out["temperatura"] - 22).clip(lower=0)
    radians = np.deg2rad(out["kierunek_wiatru"].mod(360))
    out["wiatr_sin"] = np.sin(radians)
    out["wiatr_cos"] = np.cos(radians)
    out["wiek_prognozy_h"] = frame["wiek_prognozy_h"]
    return out.replace([np.inf, -np.inf], np.nan)


def model_columns(variant: str, branch_codes: list[str], available: set[str]) -> list[str]:
    columns = [*CALENDAR_COLUMNS, *[f"oddzial_{code}" for code in branch_codes]]
    weather = [f for group in VARIANTS[variant] for f in WEATHER_GROUPS[group] if f in available]
    return [*columns, *weather, *(["wiek_prognozy_h"] if weather else [])]


def _new_model(settings: TrainingSettings, parameters: dict):
    from sklearn.ensemble import HistGradientBoostingRegressor
    # Dobór parametrów ma osobne chronologiczne okno. Brak losowej walidacji fit().
    return HistGradientBoostingRegressor(
        max_iter=settings.max_iter, early_stopping=False,
        min_samples_leaf=20, l2_regularization=2.0,
        random_state=settings.random_state, **parameters,
    )


def _predict(model, features: pd.DataFrame, columns: list[str]) -> np.ndarray:
    result = np.asarray(model.predict(features[columns]), dtype=float)
    if not np.isfinite(result).all():
        raise ValueError("Model zwrócił niefinitywną predykcję.")
    return result.clip(min=0)


def _macro_error(actual: pd.DataFrame, predicted: np.ndarray, scales: pd.Series) -> float:
    errors = pd.Series(np.abs(predicted - actual["wartosc_rzeczywista"].to_numpy()), index=actual.index)
    per_branch = errors.groupby(actual["oddzial_code"]).mean()
    return float((per_branch / scales.reindex(per_branch.index).clip(lower=1)).mean())


def _profile(frame: pd.DataFrame, timezone_name: str) -> pd.DataFrame:
    data = frame.copy()
    local = data["target_time_utc"].dt.tz_convert(timezone_name)
    data["godzina"] = local.dt.hour
    data["dzien_tygodnia"] = local.dt.dayofweek
    return data.groupby(["oddzial_code", "kierunek_energii_norm", "dzien_tygodnia", "godzina"], as_index=False)["wartosc_rzeczywista"].median()


def profile_predictions(frame: pd.DataFrame, profile: pd.DataFrame, timezone_name: str) -> np.ndarray:
    requests = frame[["oddzial_code", "kierunek_energii_norm"]].copy()
    local = frame["target_time_utc"].dt.tz_convert(timezone_name)
    requests["godzina"] = local.dt.hour
    requests["dzien_tygodnia"] = local.dt.dayofweek
    requests["_row"] = np.arange(len(frame))
    keys = ["oddzial_code", "kierunek_energii_norm", "dzien_tygodnia", "godzina"]
    joined = requests.merge(profile, how="left", on=keys, validate="many_to_one").sort_values("_row")
    return joined["wartosc_rzeczywista"].to_numpy(dtype=float)


def metrics_table(frame: pd.DataFrame, actual: str, predicted: str, kind: str) -> pd.DataFrame:
    rows = []
    for branch, part in frame.groupby("oddzial_code"):
        maximum = max(1, int(np.ceil(frame["horyzont_h"].max())))
        scopes = [(f"1-{maximum}", part)]
        for lo in range(0, maximum, 24):
            hi = min(lo + 24, maximum)
            scopes.append((f"{lo + 1}-{hi}", part.loc[part["horyzont_h"].gt(lo) & part["horyzont_h"].le(hi)]))
        for horizon, group in scopes:
            y = group[actual].to_numpy(dtype=float)
            p = group[predicted].to_numpy(dtype=float)
            mask = np.isfinite(y) & np.isfinite(p)
            y, p = y[mask], p[mask]
            if len(y):
                error = p - y
                rows.append({"model": kind, "oddzial_code": branch, "horyzont": horizon,
                             "n": len(y), "mae": float(np.abs(error).mean()),
                             "rmse": float(np.sqrt(np.mean(error ** 2))), "bias": float(error.mean()),
                             "wape": float(np.abs(error).sum() / np.abs(y).sum()) if np.abs(y).sum() else np.nan})
    return pd.DataFrame(rows)


def balance_table(frame: pd.DataFrame, value_columns: list[str]) -> pd.DataFrame:
    keys = ["oddzial_code", "punkt", "forecast_origin_utc", "target_time_utc", "horyzont_h"]
    for optional in ["doba_handlowa", "godzina_handlowa"]:
        if optional in frame.columns:
            keys.append(optional)
    parts = []
    for direction, label in [("POBRANIE", "pobranie"), ("ODDANIE", "oddanie")]:
        columns = [*keys, *value_columns]
        piece = frame.loc[frame["kierunek_energii_norm"].eq(direction), columns].copy()
        piece = piece.rename(columns={col: f"{label}_{col}" for col in value_columns})
        parts.append(piece)
    out = parts[0].merge(parts[1], on=keys, how="outer", validate="one_to_one")
    for col in value_columns:
        # Brak kierunku to brak bilansu, a nie domniemane zero.
        out[f"bilans_{col}"] = out[f"pobranie_{col}"] - out[f"oddanie_{col}"]
    return out.sort_values(["forecast_origin_utc", "target_time_utc", "oddzial_code"])


def _cap(frame: pd.DataFrame, maximum: int) -> pd.DataFrame:
    return frame.sort_values(["target_time_utc", "forecast_origin_utc"]).tail(maximum)


def train_branch_models(energy: pd.DataFrame, weather: pd.DataFrame, settings: TrainingSettings):
    settings.validate()
    if energy.empty:
        raise ValueError("Brak danych energii po filtrze dat.")
    requests = historical_requests(energy, settings.horizon_hours, settings.origin_step_hours)
    joined = join_weather_asof(requests, weather)
    usable = joined["wartosc_rzeczywista"].notna() & joined["pogoda_dopasowana"]
    data = joined.loc[usable].copy().reset_index(drop=True)
    if data.empty:
        raise ValueError("Nie ma wspólnej historii energii i prognoz pogody dostępnych przed uruchomieniem.")
    branches = sorted(energy["oddzial_code"].unique().tolist())
    cutoff = data["target_time_utc"].max().floor("D")
    test_start = cutoff - pd.Timedelta(days=settings.test_days)
    validation_start = test_start - pd.Timedelta(days=settings.validation_days)
    embargo = pd.Timedelta(hours=settings.label_delay_hours)
    train = data.loc[data["target_time_utc"].lt(validation_start - embargo)]
    validation = data.loc[data["forecast_origin_utc"].ge(validation_start)
                          & data["target_time_utc"].lt(test_start - embargo)]
    test = data.loc[data["forecast_origin_utc"].ge(test_start)]
    if train.empty or validation.empty or test.empty:
        raise ValueError("Historia za krótka do osobnych okien uczenia, doboru pogody i testu 72 h. Zmniejsz validation-days/test-days albo dodaj historię.")
    features = feature_frame(data, branches, settings.timezone)
    # Dostępność cech ustalamy wyłącznie na początkowej części historii.
    available = set(features.columns[features.loc[train.index].notna().any()])
    grid = [{"learning_rate": 0.06, "max_leaf_nodes": 15}]
    if settings.tune:
        grid.append({"learning_rate": 0.1, "max_leaf_nodes": 31})
    chosen = {}
    comparisons, importance_rows = [], []
    test_predictions = test.copy()
    test_predictions["wartosc_przewidywana"] = np.nan
    test_predictions["wartosc_bazowa"] = np.nan
    for direction in DIRECTIONS:
        fit_rows = _cap(train.loc[train["kierunek_energii_norm"].eq(direction)], settings.max_train_rows)
        val = validation.loc[validation["kierunek_energii_norm"].eq(direction)]
        heldout = test.loc[test["kierunek_energii_norm"].eq(direction)]
        if len(fit_rows) < settings.min_train_rows or val.empty or heldout.empty:
            raise ValueError(f"Za mało danych do nauki/walidacji/testu kierunku {direction}.")
        if set(fit_rows["oddzial_code"]) != set(branches):
            raise ValueError(f"Kierunek {direction} nie ma historii dla wszystkich oddziałów. Nie zakładamy zerowego oddania.")
        scales = fit_rows.groupby("oddzial_code")["wartosc_rzeczywista"].mean()
        best_score = float("inf")
        used_specs = set()
        for variant in VARIANTS:
            cols = model_columns(variant, branches, available)
            if tuple(cols) in used_specs:
                continue
            used_specs.add(tuple(cols))
            for parameters in grid:
                model = _new_model(settings, parameters)
                model.fit(features.loc[fit_rows.index, cols], fit_rows["wartosc_rzeczywista"])
                prediction = _predict(model, features.loc[val.index], cols)
                score = _macro_error(val, prediction, scales)
                comparisons.append({"kierunek": direction, "wariant": variant, **parameters,
                                    "walidacja_macro_mae_norm": score,
                                    "walidacja_mae": float(np.abs(prediction - val["wartosc_rzeczywista"].to_numpy()).mean()),
                                    "liczba_cech": len(cols), "liczba_wierszy": len(val)})
                if score < best_score:
                    best_score = score
                    chosen[direction] = {"wariant": variant, "columns": cols, "parameters": parameters}
        choice = chosen[direction]
        # Holdout nie bierze udziału w wyborze wariantu/parametrów.
        before_test = data.loc[data["target_time_utc"].lt(test_start - embargo)
                               & data["kierunek_energii_norm"].eq(direction)]
        before_test = _cap(before_test, settings.max_train_rows)
        model = _new_model(settings, choice["parameters"])
        model.fit(features.loc[before_test.index, choice["columns"]], before_test["wartosc_rzeczywista"])
        test_predictions.loc[heldout.index, "wartosc_przewidywana"] = _predict(model, features.loc[heldout.index], choice["columns"])
        test_predictions.loc[heldout.index, "wartosc_bazowa"] = profile_predictions(heldout, _profile(before_test, settings.timezone), settings.timezone)
        if settings.importance_rows > 0:
            sample = heldout.iloc[np.linspace(0, len(heldout) - 1, min(len(heldout), settings.importance_rows), dtype=int)]
            sample_x = features.loc[sample.index]
            base_error = _macro_error(sample, _predict(model, sample_x, choice["columns"]), scales)
            rng = np.random.default_rng(settings.random_state)
            for group, group_columns in WEATHER_GROUPS.items():
                used = [c for c in group_columns if c in choice["columns"]]
                if not used:
                    continue
                scores = []
                for _ in range(3):
                    shuffled = sample_x.copy()
                    for _, branch_sample in sample.groupby("oddzial_code"):
                        index = branch_sample.index
                        shuffled.loc[index, used] = sample_x.loc[rng.permutation(index), used].to_numpy()
                    scores.append(_macro_error(sample, _predict(model, shuffled, choice["columns"]), scales) - base_error)
                importance_rows.append({"kierunek": direction, "grupa_pogody": group,
                                        "wzrost_macro_mae_norm": float(np.mean(scores)),
                                        "odchylenie": float(np.std(scores)), "n": len(sample)})
        # Końcowy model zapisujemy dopiero po zamknięciu oceny holdout.
        final_rows = _cap(data.loc[data["kierunek_energii_norm"].eq(direction)], settings.max_train_rows)
        final_model = _new_model(settings, choice["parameters"])
        final_model.fit(features.loc[final_rows.index, choice["columns"]], final_rows["wartosc_rzeczywista"])
        choice["model"] = final_model
    direction_metrics = []
    for direction in DIRECTIONS:
        subset = test_predictions.loc[test_predictions["kierunek_energii_norm"].eq(direction)]
        for col, label in [("wartosc_przewidywana", "ML"), ("wartosc_bazowa", "PROFIL_HISTORYCZNY")]:
            part = metrics_table(subset, "wartosc_rzeczywista", col, label)
            part["kierunek"] = direction
            direction_metrics.append(part)
    balance = balance_table(test_predictions, ["wartosc_rzeczywista", "wartosc_przewidywana", "wartosc_bazowa"])
    for col, label in [("bilans_wartosc_przewidywana", "ML"), ("bilans_wartosc_bazowa", "PROFIL_HISTORYCZNY")]:
        part = metrics_table(balance, "bilans_wartosc_rzeczywista", col, label)
        part["kierunek"] = "BILANS"
        direction_metrics.append(part)
    branch_table = energy[["oddzial_code", "punkt"]].drop_duplicates()
    if branch_table["oddzial_code"].duplicated().any():
        raise ValueError("Oddział ma kilka punktów pogodowych.")
    bundle = {"format_version": 2, "settings": asdict(settings), "branches": branch_table,
              "models": chosen, "training_target_max_utc": str(data["target_time_utc"].max()),
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "baseline_profile": _profile(data, settings.timezone)}
    quality = pd.DataFrame([
        ["zlecenia_historyczne", len(joined), f"origin co {settings.origin_step_hours} h, horyzont {settings.horizon_hours} h"],
        ["zlecenia_bez_pogody_asof", int((~joined["pogoda_dopasowana"]).sum()), "nie użyto pogody opublikowanej po origin"],
        ["poczatek_wspolnej_historii", 0, str(data["target_time_utc"].min())],
        ["poczatek_walidacji", 0, str(validation_start)],
        ["poczatek_testu", 0, str(test_start)],
        ["embargo_pomiarow_h", settings.label_delay_hours, "założenie; Excel nie ma czasu udostępnienia korekty"],
        ["backtest_retrospektywny", 1, "targety są najlepszymi wersjami aktualnego eksportu; historyczna dostępność korekt nie jest znana"],
        ["lagi_energii", 0, "wyłączone do otrzymania historii czasów dostępności korekt"],
    ], columns=["kontrola", "liczba", "szczegoly"])
    return bundle, {
        "porownanie_wariantow": pd.DataFrame(comparisons),
        "waznosc_pogody": pd.DataFrame(importance_rows, columns=["kierunek", "grupa_pogody", "wzrost_macro_mae_norm", "odchylenie", "n"]),
        "metryki_test": pd.concat(direction_metrics, ignore_index=True),
        "backtest_bilans": balance, "kontrola_modelu": quality,
    }


def save_bundle(bundle: dict, directory: str | Path):
    import joblib
    import sklearn
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    bundle["versions"] = {"python": platform.python_version(), "sklearn": sklearn.__version__,
                           "numpy": np.__version__, "pandas": pd.__version__, "joblib": joblib.__version__}
    temporary = target / "modele_oddzialow.joblib.tmp"
    joblib.dump(bundle, temporary)
    temporary.replace(target / "modele_oddzialow.joblib")
    manifest = {k: v for k, v in bundle.items() if k not in {"models", "branches", "baseline_profile", "portfolio_reports"}}
    if bundle.get("portfolio_reports"):
        manifest["portfolio_summary"] = json.loads(bundle["portfolio_reports"]["podsumowanie_portfela"].to_json(orient="records", date_format="iso"))
    manifest["branches"] = bundle["branches"].to_dict("records")
    manifest["models"] = {d: {k: v for k, v in spec.items() if k != "model"} for d, spec in bundle["models"].items()}
    (target / "manifest_oddzialy.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def load_bundle(directory: str | Path) -> dict:
    import joblib
    import sklearn
    bundle = joblib.load(Path(directory) / "modele_oddzialow.joblib")
    if bundle.get("format_version") not in {1, 2}:
        raise ValueError("Nieobsługiwana wersja plików modelu.")
    if bundle.get("versions", {}).get("sklearn") != sklearn.__version__:
        raise ValueError("Wersja scikit-learn różni się od wersji treningu. Użyj wersji z manifestu albo wykonaj trening ponownie.")
    return bundle


def predict_branches(bundle: dict, weather: pd.DataFrame, origin: object, horizon: int | None = None,
                     portfolio_reports: dict | None = None):
    settings = TrainingSettings(**bundle["settings"])
    hours = horizon if horizon is not None else settings.horizon_hours
    if hours > settings.horizon_hours:
        raise ValueError("Horyzont przekracza zakres użyty w treningu.")
    asof = utc_timestamp(origin, settings.timezone)
    if asof <= utc_timestamp(bundle["training_target_max_utc"]):
        raise ValueError("Wybrany czas prognozy jest wcześniejszy od końca historii treningowej. Do testów historycznych użyj backtestu.")
    requests = future_requests(bundle["branches"], asof, hours, settings.timezone)
    joined = join_weather_asof(requests, weather)
    features = feature_frame(joined, sorted(bundle["branches"]["oddzial_code"].unique()), settings.timezone)
    forecasts = []
    for direction in DIRECTIONS:
        choice = bundle["models"][direction]
        part = joined.copy()
        part["kierunek_energii_norm"] = direction
        part["wartosc_przewidywana"] = np.nan
        required_weather = [c for c in choice["columns"] if c not in CALENDAR_COLUMNS and not c.startswith("oddzial_")]
        mask = part["pogoda_dopasowana"] if required_weather else pd.Series(True, index=part.index)
        substantive = [c for c in required_weather if c != "wiek_prognozy_h"]
        if substantive:
            mask = mask & features[substantive].notna().any(axis=1)
        part["status"] = np.where(mask, "PROGNOZA", "BRAK_POGODY_PRZED_URUCHOMIENIEM")
        if mask.any():
            part.loc[mask, "wartosc_przewidywana"] = _predict(choice["model"], features.loc[mask], choice["columns"])
        part["wartosc_bazowa"] = profile_predictions(part, bundle["baseline_profile"], settings.timezone)
        forecasts.append(part)
    details = pd.concat(forecasts, ignore_index=True)
    balance = balance_table(details, ["wartosc_przewidywana", "wartosc_bazowa"])
    balance = balance.rename(columns={"pobranie_wartosc_przewidywana": "pobranie",
                                      "oddanie_wartosc_przewidywana": "oddanie",
                                      "bilans_wartosc_przewidywana": "bilans"})
    balance["status"] = np.where(balance["bilans"].notna(), "PROGNOZA", "BRAK_PROGNOZY_KIERUNKU")
    from .portfolio import annotate_portfolio
    effective_reports = portfolio_reports if portfolio_reports is not None else bundle.get("portfolio_reports")
    return annotate_portfolio(balance, details, effective_reports, asof,
                              grace_days=bundle.get("portfolio_grace_days", 14),
                              source="AKTUALNY_EKSPORT" if portfolio_reports is not None else "HISTORIA_TRENINGU",
                              baseline_reports=bundle.get("portfolio_reports") if portfolio_reports is not None else None)
