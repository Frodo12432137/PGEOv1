"""Uczenie zapisanych modeli oddziałów i szybka predykcja bez fit()."""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from pathlib import Path
import json
import hashlib
import platform
from importlib.util import find_spec
from importlib.metadata import version, PackageNotFoundError
from time import perf_counter

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
    "temperatura": ["temperatura"],
    "slonce": ["slonce"],
    "temperatura_slonce": ["temperatura", "slonce"],
    "pogoda_podstawowa": ["temperatura", "slonce", "chmury", "wiatr"],
    "pogoda_pelna": list(WEATHER_GROUPS),
}
CALENDAR_COLUMNS = ["godzina", "dzien_tygodnia", "miesiac", "weekend", "swieto_PL",
                    "godzina_sin", "godzina_cos", "rok_sin", "rok_cos", "horyzont_h",
                    "godzina_tygodnia", "dzien_roku", "przed_swietem", "po_swiecie"]
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
    model_backend: str = "hist_gradient_boosting"
    learning_rate: float = 0.06
    max_leaf_nodes: int = 15
    min_samples_leaf: int = 20
    l2_regularization: float = 2.0
    catboost_depth: int = 6
    catboost_l2_leaf_reg: float = 5.0
    lightgbm_num_leaves: int = 31
    xgboost_max_depth: int = 6
    ensemble: bool = False
    threads: int = 4
    min_evaluation_targets: int = 24
    weather_variants: tuple[str, ...] = tuple(VARIANTS)

    def validate(self):
        if not 1 <= self.horizon_hours <= 168:
            raise ValueError("Horyzont musi należeć do zakresu 1–168 godzin.")
        if min(self.origin_step_hours, self.validation_days, self.test_days, self.min_train_rows, self.max_iter) < 1:
            raise ValueError("Odstępy czasu, rozmiary okien i liczba iteracji muszą być dodatnie.")
        if self.max_train_rows < self.min_train_rows or self.label_delay_hours < 0:
            raise ValueError("Niepoprawny limit danych lub opóźnienie pomiarów.")
        if self.model_backend not in {"auto", "hist_gradient_boosting", "catboost", "lightgbm", "xgboost"}:
            raise ValueError("Model: auto, hist_gradient_boosting, catboost, lightgbm albo xgboost.")
        if not np.isfinite(self.learning_rate) or not 0 < self.learning_rate <= 1:
            raise ValueError("learning_rate musi należeć do (0, 1].")
        if self.max_leaf_nodes < 2 or min(self.min_samples_leaf, self.threads, self.min_evaluation_targets) < 1:
            raise ValueError("Liczba liści, obserwacji, wątków i celów oceny musi być dodatnia.")
        if not 1 <= self.catboost_depth <= 12:
            raise ValueError("Głębokość CatBoost musi należeć do zakresu 1–12.")
        if not 2 <= self.lightgbm_num_leaves <= 1024 or not 1 <= self.xgboost_max_depth <= 16:
            raise ValueError("LightGBM: 2–1024 liści; XGBoost: głębokość 1–16.")
        if any(not np.isfinite(v) or v < 0 for v in [self.l2_regularization, self.catboost_l2_leaf_reg]):
            raise ValueError("Regularyzacja musi być nieujemna i skończona.")
        if self.importance_rows < 0 or not self.weather_variants or set(self.weather_variants) - set(VARIANTS):
            raise ValueError("Niepoprawna próbka ważności albo lista wariantów pogody.")


def settings_from_options(options: dict) -> TrainingSettings:
    """Te same ustawienia co launcher produkcyjny, bez drugiej implementacji modelu."""
    values = dict(options)
    if "models" in values:
        values["model_backend"] = values.pop("models")
    if values.pop("skip_importance", False):
        values["importance_rows"] = 0
    if "weather_variants" in values:
        values["weather_variants"] = tuple(values["weather_variants"])
    names = {field.name for field in fields(TrainingSettings)}
    extra = set(values) - names - {"start_date", "include_dst_days", "sheet_regex", "portfolio_grace_days",
                                  "exclude_negative_energy", "exclude_energy_outliers", "energy_outlier_iqr_multiplier",
                                  "energy_outlier_min_ratio", "energy_outlier_min_samples"}
    if extra:
        raise ValueError(f"Nieznane parametry modelu: {sorted(extra)}")
    result = TrainingSettings(**{key: value for key, value in values.items() if key in names})
    result.validate()
    return result


def feature_frame(frame: pd.DataFrame, branch_codes: list[str], timezone_name: str) -> pd.DataFrame:
    ts = frame["target_time_utc"].dt.tz_convert(timezone_name)
    out = pd.DataFrame(index=frame.index)
    out["godzina"] = ts.dt.hour.astype(float)
    out["dzien_tygodnia"] = ts.dt.dayofweek.astype(float)
    out["miesiac"] = ts.dt.month.astype(float)
    out["weekend"] = ts.dt.dayofweek.ge(5).astype(float)
    holidays = set().union(*[_polish_holidays(y) for y in range(int(ts.dt.year.min()) - 1, int(ts.dt.year.max()) + 2)])
    out["swieto_PL"] = ts.dt.date.isin(holidays).astype(float)
    out["godzina_tygodnia"] = ts.dt.dayofweek * 24 + ts.dt.hour
    out["dzien_roku"] = ts.dt.dayofyear
    dates = ts.dt.tz_localize(None).dt.normalize()
    out["przed_swietem"] = (dates + pd.Timedelta(days=1)).dt.date.isin(holidays).astype(float)
    out["po_swiecie"] = (dates - pd.Timedelta(days=1)).dt.date.isin(holidays).astype(float)
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
    return out.replace([np.inf, -np.inf], np.nan).astype(float)


def model_columns(variant: str, branch_codes: list[str], available: set[str]) -> list[str]:
    columns = [*CALENDAR_COLUMNS, *[f"oddzial_{code}" for code in branch_codes]]
    weather = [f for group in VARIANTS[variant] for f in WEATHER_GROUPS[group] if f in available]
    return [*columns, *weather, *(["wiek_prognozy_h"] if weather else [])]


class ValidationEnsemble:
    """Wagi wybrane na walidacji; członkowie są refitowani na tych samych foldach."""

    def __init__(self, settings: TrainingSettings, members: list[dict], weights: list[float]):
        self.settings, self.members, self.weights = settings, members, weights

    def fit(self, features, target):
        self.models = []
        for member in self.members:
            model = _new_model(self.settings, member["parameters"])
            model.fit(features[member["columns"]], target)
            self.models.append(model)
        return self

    def predict(self, features):
        return sum(weight * _predict(model, features, member["columns"])
                   for weight, member, model in zip(self.weights, self.members, self.models))


def _new_model(settings: TrainingSettings, parameters: dict):
    parameters = parameters.copy()
    backend = parameters.pop("backend", "hist_gradient_boosting")
    if backend == "ensemble":
        return ValidationEnsemble(settings, **parameters)
    if backend == "catboost":
        try:
            from catboost import CatBoostRegressor
        except ImportError as exc:
            raise RuntimeError("CatBoost nie jest zainstalowany. Uruchom: python -m pip install -r mdd_forecasting/requirements-production.txt") from exc
        return CatBoostRegressor(iterations=settings.max_iter, loss_function="RMSE",
                                 random_seed=settings.random_state, thread_count=settings.threads,
                                 allow_writing_files=False, verbose=False, **parameters)
    if backend == "lightgbm":
        try:
            from lightgbm import LGBMRegressor
        except ImportError as exc:
            raise RuntimeError("Brak LightGBM. Zainstaluj requirements-production.txt.") from exc
        return LGBMRegressor(n_estimators=settings.max_iter, objective="regression", verbosity=-1,
                             random_state=settings.random_state, n_jobs=settings.threads,
                             deterministic=True, force_col_wise=True, **parameters)
    if backend == "xgboost":
        try:
            from xgboost import XGBRegressor
        except ImportError as exc:
            raise RuntimeError("Brak XGBoost. Zainstaluj requirements-production.txt.") from exc
        return XGBRegressor(n_estimators=settings.max_iter, objective="reg:squarederror", tree_method="hist",
                            random_state=settings.random_state, n_jobs=settings.threads, **parameters)
    from sklearn.ensemble import HistGradientBoostingRegressor
    # Dobór parametrów ma osobne chronologiczne okno. Brak losowej walidacji fit().
    return HistGradientBoostingRegressor(
        max_iter=settings.max_iter, early_stopping=False,
        random_state=settings.random_state, **parameters,
    )


def candidate_parameters(settings: TrainingSettings) -> list[dict]:
    """Jawny, ograniczony grid; żaden parametr nie jest dobierany na teście."""
    backends = [settings.model_backend]
    if settings.model_backend == "auto":
        backends = ["hist_gradient_boosting"]
        for backend in ["catboost", "lightgbm", "xgboost"]:
            if find_spec(backend) is not None:
                backends.append(backend)
            else:
                print(f"Brak {backend}: rodzina poza porównaniem. Instalacja: requirements-production.txt", flush=True)
    candidates = []
    for backend in backends:
        if backend == "hist_gradient_boosting":
            base = {"backend": backend, "learning_rate": settings.learning_rate,
                    "max_leaf_nodes": settings.max_leaf_nodes, "min_samples_leaf": settings.min_samples_leaf,
                    "l2_regularization": settings.l2_regularization}
            candidates.append(base)
            if settings.tune:
                candidates.extend([{**base, "learning_rate": settings.learning_rate / 2,
                                    "max_leaf_nodes": max(2, settings.max_leaf_nodes // 2),
                                    "l2_regularization": settings.l2_regularization * 2},
                                   {**base, "max_leaf_nodes": settings.max_leaf_nodes * 2 + 1}])
        elif backend == "catboost":
            base = {"backend": backend, "learning_rate": settings.learning_rate,
                    "depth": settings.catboost_depth, "l2_leaf_reg": settings.catboost_l2_leaf_reg}
            candidates.append(base)
            if settings.tune:
                candidates.extend([{**base, "depth": max(1, settings.catboost_depth - 2)},
                                   {**base, "depth": min(12, settings.catboost_depth + 2),
                                    "learning_rate": settings.learning_rate / 2}])
        elif backend == "lightgbm":
            base = {"backend": backend, "learning_rate": settings.learning_rate,
                    "num_leaves": settings.lightgbm_num_leaves, "min_child_samples": settings.min_samples_leaf,
                    "reg_lambda": settings.l2_regularization}
            candidates.append(base)
            if settings.tune:
                candidates.extend([{**base, "num_leaves": max(2, settings.lightgbm_num_leaves // 2),
                                    "reg_lambda": settings.l2_regularization * 2},
                                   {**base, "num_leaves": min(1024, settings.lightgbm_num_leaves * 2),
                                    "learning_rate": settings.learning_rate / 2}])
        else:
            base = {"backend": backend, "learning_rate": settings.learning_rate,
                    "max_depth": settings.xgboost_max_depth, "min_child_weight": 10,
                    "reg_lambda": settings.l2_regularization}
            candidates.append(base)
            if settings.tune:
                candidates.extend([{**base, "max_depth": max(1, settings.xgboost_max_depth - 2),
                                    "reg_lambda": settings.l2_regularization * 2},
                                   {**base, "max_depth": min(16, settings.xgboost_max_depth + 2),
                                    "learning_rate": settings.learning_rate / 2}])
    return candidates


def _predict(model, features: pd.DataFrame, columns: list[str]) -> np.ndarray:
    result = np.asarray(model.predict(features[columns]), dtype=float)
    if not np.isfinite(result).all():
        raise ValueError("Model zwrócił niefinitywną predykcję.")
    return result.clip(min=0)


def _macro_error(actual: pd.DataFrame, predicted: np.ndarray, scales: pd.Series) -> float:
    errors = pd.Series(np.abs(predicted - actual["wartosc_rzeczywista"].to_numpy()), index=actual.index)
    per_branch = errors.groupby(actual["oddzial_code"]).mean()
    return float((per_branch / scales.reindex(per_branch.index).clip(lower=1e-12)).mean())


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
    return joined["wartosc_rzeczywista"].to_numpy(dtype=float, na_value=np.nan)


def metrics_table(frame: pd.DataFrame, actual: str, predicted: str, kind: str) -> pd.DataFrame:
    rows = []
    for branch, part in frame.groupby("oddzial_code"):
        maximum = max(1, int(np.ceil(frame["horyzont_h"].max())))
        scopes = [(f"1-{maximum}", part)]
        for lo in range(0, maximum, 24):
            hi = min(lo + 24, maximum)
            if lo == 0 and hi == maximum:
                continue
            scopes.append((f"{lo + 1}-{hi}", part.loc[part["horyzont_h"].gt(lo) & part["horyzont_h"].le(hi)]))
        for horizon, group in scopes:
            y = group[actual].to_numpy(dtype=float, na_value=np.nan)
            p = group[predicted].to_numpy(dtype=float, na_value=np.nan)
            mask = np.isfinite(y) & np.isfinite(p)
            y, p = y[mask], p[mask]
            if len(y):
                error = p - y
                rows.append({"model": kind, "oddzial_code": branch, "horyzont": horizon,
                             "n": len(y), "n_oczekiwane": len(group), "pokrycie": len(y) / len(group),
                             "mae": float(np.abs(error).mean()),
                             "rmse": float(np.sqrt(np.mean(error ** 2))), "bias": float(error.mean()),
                             "mediana_bledu_abs": float(np.median(np.abs(error))),
                             "p90_bledu_abs": float(np.quantile(np.abs(error), .9)),
                             "p95_bledu_abs": float(np.quantile(np.abs(error), .95)),
                             "max_bledu_abs": float(np.abs(error).max()),
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


def _select_ensemble(settings, specs, features, fit_rows, validation, scales, choice, prediction, notify):
    """Porównaj blend wyłącznie z wcześniejszą walidacją; holdout nie jest argumentem."""
    ordered = sorted(specs, key=lambda spec: spec["score"])
    first = ordered[0]
    others = ordered[1:]
    different_family = [spec for spec in others if spec["parameters"]["backend"] != first["parameters"]["backend"]]
    second = (different_family or others)[0]
    members = [{key: spec[key] for key in ["parameters", "columns", "wariant"]} for spec in [first, second]]
    started = perf_counter()
    notify("WALIDACJA_ZESPOLU", modele=[member["parameters"]["backend"] for member in members])
    predictions = []
    for member in members:
        model = _new_model(settings, member["parameters"])
        model.fit(features.loc[fit_rows.index, member["columns"]], fit_rows["wartosc_rzeczywista"])
        predictions.append(_predict(model, features.loc[validation.index], member["columns"]))
    columns = list(dict.fromkeys(column for member in members for column in member["columns"]))
    best_score = first["score"]
    prediction = predictions[0]
    rows = []
    for weight in [.25, .5, .75]:
        combined = weight * predictions[0] + (1 - weight) * predictions[1]
        score = _macro_error(validation, combined, scales)
        parameters = {"backend": "ensemble", "members": members, "weights": [weight, 1 - weight]}
        rows.append({"kierunek": fit_rows.kierunek_energii_norm.iloc[0], "wariant": "zespol", **parameters,
                     "walidacja_macro_mae_norm": score,
                     "walidacja_mae": float(np.abs(combined - validation.wartosc_rzeczywista.to_numpy()).mean()),
                     "liczba_cech": len(columns), "liczba_wierszy": len(validation), "czas_s": perf_counter() - started})
        if score < best_score:
            best_score, prediction = score, combined
            choice = {"wariant": "zespol", "parameters": parameters, "columns": columns}
    return choice, prediction, best_score, rows


def train_branch_models(energy: pd.DataFrame, weather: pd.DataFrame, settings: TrainingSettings,
                        *, trial_cache: dict | None = None, progress=None, split_cutoff=None):
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
    cutoff = data["target_time_utc"].max().floor("D") if split_cutoff is None else utc_timestamp(split_cutoff).floor("D")
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
    grid = candidate_parameters(settings)
    def notify(stage, **data):
        if progress is not None:
            progress({"etap": stage, **data})

    notify("PRZYGOTOWANIE_UCZENIA", maks_prob=len(DIRECTIONS) * (len(grid) * len(settings.weather_variants) + (3 if settings.ensemble else 0)),
           wiersze=len(data))
    chosen = {}
    comparisons, importance_rows, coverage_rows, recommendations, validation_rows = [], [], [], [], []
    test_predictions = test.copy()
    test_predictions["wartosc_przewidywana"] = np.nan
    test_predictions["wartosc_bazowa"] = np.nan
    for direction in DIRECTIONS:
        fit_rows = _cap(train.loc[train["kierunek_energii_norm"].eq(direction)], settings.max_train_rows)
        val = validation.loc[validation["kierunek_energii_norm"].eq(direction)]
        heldout = test.loc[test["kierunek_energii_norm"].eq(direction)]
        if len(fit_rows) < settings.min_train_rows or val.empty or heldout.empty:
            raise ValueError(f"Za mało danych do nauki/walidacji/testu kierunku {direction}.")
        for stage, stage_data in [("trening", fit_rows), ("walidacja", val), ("test", heldout)]:
            counts = stage_data.groupby("oddzial_code")["target_time_utc"].nunique().reindex(branches, fill_value=0)
            minimum = 1 if stage == "trening" else settings.min_evaluation_targets
            for branch, count in counts.items():
                coverage_rows.append({"etap": stage, "kierunek": direction, "oddzial_code": branch,
                                      "unikalne_godziny": int(count), "minimum": minimum})
            insufficient = counts.loc[counts.lt(minimum)]
            if not insufficient.empty:
                raise ValueError(f"Brak wystarczającego pokrycia: {stage}, {direction}, oddziały {insufficient.to_dict()}. Minimum: {minimum} unikalnych godzin; nie zakładamy zera.")
        scales = fit_rows.groupby("oddzial_code")["wartosc_rzeczywista"].mean()
        best_score = float("inf")
        best_prediction = None
        candidate_specs = []
        used_specs = set()
        for variant in settings.weather_variants:
            cols = model_columns(variant, branches, available)
            if tuple(cols) in used_specs:
                continue
            used_specs.add(tuple(cols))
            for parameters in grid:
                started = perf_counter()
                cache_key = hashlib.sha256(json.dumps([direction, variant, cols, parameters], sort_keys=True).encode()).hexdigest()
                cached = trial_cache.get(cache_key) if trial_cache is not None else None
                prediction = None
                notify("STROJENIE", kierunek=direction, wariant=variant, parametry=parameters,
                       gotowe_proby=len(comparisons), z_cache=cached is not None)
                if cached is not None:
                    record = dict(cached)
                    if record["liczba_cech"] != len(cols) or record["liczba_wierszy"] != len(val):
                        raise ValueError("Cache strojenia nie pasuje do danych. Uruchom backtest bez wznowienia.")
                    score = float(record["walidacja_macro_mae_norm"])
                    if not np.isfinite(score):
                        raise ValueError("Niepoprawny wynik w cache strojenia. Uruchom backtest bez wznowienia.")
                    print(f"{direction}: wznawiam gotowy wynik {parameters['backend']} / {variant}", flush=True)
                else:
                    print(f"{direction}: {parameters['backend']}, pogoda={variant}, parametry={parameters}", flush=True)
                    model = _new_model(settings, parameters)
                    model.fit(features.loc[fit_rows.index, cols], fit_rows["wartosc_rzeczywista"])
                    prediction = _predict(model, features.loc[val.index], cols)
                    score = _macro_error(val, prediction, scales)
                    record = {"kierunek": direction, "wariant": variant, **parameters,
                              "walidacja_macro_mae_norm": score,
                              "walidacja_mae": float(np.abs(prediction - val["wartosc_rzeczywista"].to_numpy()).mean()),
                              "liczba_cech": len(cols), "liczba_wierszy": len(val),
                              "czas_s": perf_counter() - started}
                    if trial_cache is not None:
                        trial_cache[cache_key] = dict(record)
                comparisons.append(record)
                candidate_specs.append({"score": score, "wariant": variant, "columns": cols, "parameters": parameters})
                notify("PROBA_GOTOWA", gotowe_proby=len(comparisons), wynik=record, z_cache=cached is not None)
                if score < best_score:
                    best_score = score
                    best_prediction = prediction
                    chosen[direction] = {"wariant": variant, "columns": cols, "parameters": parameters}
        choice = chosen[direction]
        if settings.ensemble and len(candidate_specs) >= 2:
            choice, best_prediction, best_score, blend_rows = _select_ensemble(
                settings, candidate_specs, features, fit_rows, val, scales, choice, best_prediction, notify)
            comparisons.extend(blend_rows)
            notify("ZESPOL_POROWNANY", kierunek=direction, gotowe_proby=len(comparisons),
                   wybrany_zespol=choice["parameters"]["backend"] == "ensemble")
            chosen[direction] = choice
        if best_prediction is None:
            # Przy wznowieniu wyniki prób są zapisane, a duże modele kandydatów nie.
            notify("ODTWORZENIE_WALIDACJI", kierunek=direction)
            model = _new_model(settings, choice["parameters"])
            model.fit(features.loc[fit_rows.index, choice["columns"]], fit_rows.wartosc_rzeczywista)
            best_prediction = _predict(model, features.loc[val.index], choice["columns"])
        baseline_validation = profile_predictions(val, _profile(fit_rows, settings.timezone), settings.timezone)
        baseline_mask = np.isfinite(baseline_validation)
        baseline_score = _macro_error(val.loc[baseline_mask], baseline_validation[baseline_mask], scales) if baseline_mask.any() else np.nan
        paired_score = _macro_error(val.loc[baseline_mask], best_prediction[baseline_mask], scales) if baseline_mask.any() else np.nan
        validation_frame = val[["oddzial_code", "target_time_utc", "wartosc_rzeczywista"]].rename(columns={"wartosc_rzeczywista": "realizacja"}).copy()
        validation_frame["prognoza"], validation_frame["profil"] = best_prediction, baseline_validation
        from .branch_diagnostics import aggregate_errors
        per_branch = aggregate_errors(validation_frame, ["oddzial_code"])
        per_branch["kierunek"] = direction
        validation_rows.append(per_branch)
        recommendations.append({
            "kierunek": direction, "model": choice["parameters"]["backend"], "wariant_pogody": choice["wariant"],
            "parametry": json.dumps(choice["parameters"], ensure_ascii=False),
            "liczba_iteracji": settings.max_iter, "liczba_cech": len(choice["columns"]),
            "walidacja_macro_mae_norm": best_score, "profil_macro_mae_norm_wspolne": baseline_score,
            "ml_macro_mae_norm_wspolne": paired_score, "pokrycie_profilu_walidacja": float(baseline_mask.mean()),
            "poprawa_vs_profil_walidacja": 1 - paired_score / baseline_score if baseline_score > 0 else np.nan,
            "walidacja_od_UTC": str(val.target_time_utc.min()), "walidacja_do_UTC": str(val.target_time_utc.max()),
            "liczba_kandydatow": sum(row["kierunek"] == direction for row in comparisons),
            "zalecenie": "WYBRANY_NA_WALIDACJI" if paired_score < baseline_score else "SPRAWDZ_ML_NIE_WYGRAL_Z_PROFILEM_LUB_BRAK_POROWNANIA",
            "zasada": "Parametry zastosowane automatycznie; zewnętrzny backtest nie uczestniczy w wyborze.",
        })
        choice["validation_score"] = best_score
        print(f"{direction}: wybrano {choice['parameters']['backend']} / {choice['wariant']}, błąd walidacji={best_score:.5f}. Oceniam na osobnym teście.", flush=True)
        # Holdout nie bierze udziału w wyborze wariantu/parametrów.
        before_test = data.loc[data["target_time_utc"].lt(test_start - embargo)
                               & data["kierunek_energii_norm"].eq(direction)]
        before_test = _cap(before_test, settings.max_train_rows)
        model = _new_model(settings, choice["parameters"])
        notify("OCENA_WEWNETRZNA", kierunek=direction)
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
                    importance_rows.append({"kierunek": direction, "grupa_pogody": group,
                                            "wzrost_macro_mae_norm": np.nan, "odchylenie": np.nan,
                                            "n": len(sample), "uzyta_w_modelu": False})
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
                                        "odchylenie": float(np.std(scores)), "n": len(sample), "uzyta_w_modelu": True})
        # Końcowy model zapisujemy dopiero po zamknięciu oceny holdout.
        final_rows = _cap(data.loc[data["kierunek_energii_norm"].eq(direction)], settings.max_train_rows)
        final_model = _new_model(settings, choice["parameters"])
        notify("KONCOWE_UCZENIE", kierunek=direction, wiersze=len(final_rows))
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
    bundle = {"format_version": 4, "settings": asdict(settings), "branches": branch_table,
              "models": chosen, "training_target_max_utc": str(data["target_time_utc"].max()),
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "baseline_profile": _profile(data, settings.timezone)}
    quality = pd.DataFrame([
        ["zlecenia_historyczne", len(joined), f"origin co {settings.origin_step_hours} h, horyzont {settings.horizon_hours} h"],
        ["zlecenia_bez_pogody_asof", int((~joined["pogoda_dopasowana"]).sum()), "nie użyto pogody opublikowanej po origin"],
        ["poczatek_wspolnej_historii", 0, str(data["target_time_utc"].min())],
        ["poczatek_walidacji", 0, str(validation_start)],
        ["poczatek_testu", 0, str(test_start)],
        ["stala_granica_podzialu_UTC", 0, str(cutoff)],
        ["embargo_pomiarow_h", settings.label_delay_hours, "założenie; Excel nie ma czasu udostępnienia korekty"],
        ["backtest_retrospektywny", 1, "targety są najlepszymi wersjami aktualnego eksportu; historyczna dostępność korekt nie jest znana"],
        ["lagi_energii", 0, "wyłączone do otrzymania historii czasów dostępności korekt"],
    ], columns=["kontrola", "liczba", "szczegoly"])
    comparison = pd.DataFrame(comparisons)
    comparison["ranking_walidacji"] = comparison.groupby("kierunek")["walidacja_macro_mae_norm"].rank(method="first").astype(int)
    comparison["wybrany"] = comparison["ranking_walidacji"].eq(1)
    reports = {
        "porownanie_wariantow": comparison,
        "waznosc_pogody": pd.DataFrame(importance_rows, columns=["kierunek", "grupa_pogody", "wzrost_macro_mae_norm", "odchylenie", "n", "uzyta_w_modelu"]),
        "metryki_test": pd.concat(direction_metrics, ignore_index=True),
        "backtest_bilans": balance, "kontrola_modelu": quality, "pokrycie_okien": pd.DataFrame(coverage_rows),
        "rekomendacje_modelu": pd.DataFrame(recommendations),
        "walidacja_osd": pd.concat(validation_rows, ignore_index=True),
    }
    from .branch_diagnostics import diagnostic_tables, weather_coverage
    reports.update(diagnostic_tables(test_predictions, balance, settings.timezone))
    reports["pokrycie_pogody"] = weather_coverage(joined)
    from .weather_quality import weather_quality_tables, weather_variant_recommendations
    reports.update(weather_quality_tables(joined, settings.timezone))
    reports["rekomendacje_pogody"] = weather_variant_recommendations(comparison)
    return bundle, reports


def model_backends(parameters: dict) -> set[str]:
    if parameters.get("backend") == "ensemble":
        return set().union(*(model_backends(member["parameters"]) for member in parameters["members"]))
    return {parameters.get("backend", "hist_gradient_boosting")}


def backend_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        if name == "xgboost":
            return version("xgboost-cpu")
        raise


def save_bundle(bundle: dict, directory: str | Path):
    import joblib
    import sklearn
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    bundle["versions"] = {"python": platform.python_version(), "sklearn": sklearn.__version__,
                           "numpy": np.__version__, "pandas": pd.__version__, "joblib": joblib.__version__}
    backends = set().union(*(model_backends(spec["parameters"]) for spec in bundle["models"].values()))
    for backend in backends & {"catboost", "lightgbm", "xgboost"}:
        bundle["versions"][backend] = backend_version(backend)
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
    if bundle.get("format_version") not in {1, 2, 3, 4}:
        raise ValueError("Nieobsługiwana wersja plików modelu.")
    if bundle.get("versions", {}).get("sklearn") != sklearn.__version__:
        raise ValueError("Wersja scikit-learn różni się od wersji treningu. Użyj wersji z manifestu albo wykonaj trening ponownie.")
    for backend in {"catboost", "lightgbm", "xgboost"} & bundle.get("versions", {}).keys():
        if bundle["versions"][backend] != backend_version(backend):
            raise ValueError(f"Wersja {backend} różni się od wersji treningu. Użyj wersji z manifestu.")
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
        partial = mask & features[substantive].isna().any(axis=1) if substantive else pd.Series(False, index=part.index)
        part.loc[partial, "status"] = "PROGNOZA_NIEPELNA_POGODA"
        part["brakujace_cechy_pogody"] = features[substantive].isna().apply(
            lambda row: ", ".join(row.index[row]), axis=1) if substantive else ""
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
    partial_keys = details.loc[details["status"].eq("PROGNOZA_NIEPELNA_POGODA"),
                               ["oddzial_code", "target_time_utc"]].drop_duplicates()
    if not partial_keys.empty:
        index = pd.MultiIndex.from_frame(balance[["oddzial_code", "target_time_utc"]])
        partial_index = pd.MultiIndex.from_frame(partial_keys)
        balance.loc[index.isin(partial_index) & balance["bilans"].notna(), "status"] = "PROGNOZA_NIEPELNA_POGODA"
    from .portfolio import annotate_portfolio
    effective_reports = portfolio_reports if portfolio_reports is not None else bundle.get("portfolio_reports")
    return annotate_portfolio(balance, details, effective_reports, asof,
                              grace_days=bundle.get("portfolio_grace_days", 14),
                              source="AKTUALNY_EKSPORT" if portfolio_reports is not None else "HISTORIA_TRENINGU",
                              baseline_reports=bundle.get("portfolio_reports") if portfolio_reports is not None else None)
