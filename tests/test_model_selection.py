from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from importlib.util import find_spec
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from mdd_forecasting.branch_diagnostics import diagnostic_tables
from mdd_forecasting.branch_model import (TrainingSettings, ValidationEnsemble, _new_model, _select_ensemble,
                                         feature_frame, load_bundle, predict_branches, save_bundle,
                                         train_branch_models)
from mdd_forecasting.weather_quality import weather_quality_tables, weather_variant_recommendations
from tests import test_backtest as backtest_fixture


class TestExtendedModelSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Osobna instancja fixture nie uruchamia ponownie testów drugiej klasy.
        backtest_fixture.TestBacktest.setUpClass()
        cls.energy, cls.weather = backtest_fixture.TestBacktest.train, backtest_fixture.TestBacktest.weather
        cls.settings = replace(backtest_fixture.TestBacktest.settings, weather_variants=("kalendarz",), max_iter=3)
        cls.origin = backtest_fixture.TestBacktest.origin

    def test_ensemble_uses_validation_error_and_refits_members_on_supplied_rows(self):
        rows = pd.DataFrame({"oddzial_code": ["A", "A"], "kierunek_energii_norm": ["POBRANIE", "POBRANIE"],
                             "wartosc_rzeczywista": [10., 10.]}, index=[0, 1])
        val = pd.DataFrame({"oddzial_code": ["A", "A"], "wartosc_rzeczywista": [10., 10.]}, index=[2, 3])
        features = pd.DataFrame({"x": [1., 2., 3., 4.]})

        class Candidate:
            def __init__(self, values):
                self.values = values

            def fit(self, x, y):
                np.testing.assert_array_equal(y, [10., 10.])
                assert list(x.index) == [0, 1]  # Brak wierszy walidacji w fit().
                return self

            def predict(self, x):
                return self.values

        specs = [{"score": 1., "wariant": "kalendarz", "columns": ["x"], "parameters": {"backend": "hist_gradient_boosting"}},
                 {"score": 1., "wariant": "temperatura", "columns": ["x"], "parameters": {"backend": "lightgbm"}}]
        choice = {key: specs[0][key] for key in ["wariant", "columns", "parameters"]}
        with patch("mdd_forecasting.branch_model._new_model", side_effect=[Candidate(np.array([0., 20.])), Candidate(np.array([20., 0.]))]):
            selected, predicted, score, comparisons = _select_ensemble(
                self.settings, specs, features, rows, val, pd.Series({"A": 10.}), choice, None, lambda *a, **k: None)
        self.assertEqual(selected["parameters"]["backend"], "ensemble")
        self.assertEqual(selected["parameters"]["weights"], [.5, .5])
        self.assertEqual(score, 0.)
        np.testing.assert_array_equal(predicted, [10., 10.])
        self.assertEqual(len(comparisons), 3)

    def test_ensemble_not_selected_when_it_does_not_improve_validation(self):
        rows = pd.DataFrame({"oddzial_code": ["A"], "kierunek_energii_norm": ["POBRANIE"], "wartosc_rzeczywista": [10.]})
        features = pd.DataFrame({"x": [1.]})
        specs = [{"score": 0., "wariant": "kalendarz", "columns": ["x"], "parameters": {"backend": "hist_gradient_boosting"}},
                 {"score": 1., "wariant": "temperatura", "columns": ["x"], "parameters": {"backend": "lightgbm"}}]
        choice = {key: specs[0][key] for key in ["wariant", "columns", "parameters"]}
        from unittest.mock import Mock
        a, b = Mock(), Mock()
        a.predict.return_value, b.predict.return_value = np.array([10.]), np.array([20.])
        with patch("mdd_forecasting.branch_model._new_model", side_effect=[a, b]):
            selected, _, score, _ = _select_ensemble(self.settings, specs, features, rows, rows,
                                                    pd.Series({"A": 10.}), choice, None, lambda *a, **k: None)
        self.assertEqual(selected["parameters"]["backend"], "hist_gradient_boosting")
        self.assertEqual(score, 0.)

    def test_real_lightgbm_and_xgboost_training_storage_and_inference_without_fit(self):
        for backend in ["lightgbm", "xgboost"]:
            if find_spec(backend) is None:
                continue
            with self.subTest(backend=backend), threadpool_limits(limits=1), redirect_stdout(StringIO()):
                bundle, reports = train_branch_models(self.energy, self.weather, replace(self.settings, model_backend=backend))
                self.assertEqual(set(reports["porownanie_wariantow"].backend), {backend})
                self.assertTrue(reports["rekomendacje_modelu"].model.eq(backend).all())
                with TemporaryDirectory() as tmp:
                    save_bundle(bundle, tmp)
                    restored = load_bundle(tmp)
                    self.assertIn(backend, restored["versions"])
                    klass = type(restored["models"]["POBRANIE"]["model"])
                    with patch.object(klass, "fit", side_effect=AssertionError("fit przy predykcji")):
                        result, _ = predict_branches(restored, self.weather, self.origin)
                self.assertEqual(len(result), 72)
                self.assertTrue(result.bilans.notna().all())

    def test_real_ensemble_roundtrip_records_all_backend_versions(self):
        if find_spec("lightgbm") is None:
            self.skipTest("Opcjonalny LightGBM nie jest zainstalowany")
        settings = replace(self.settings, model_backend="auto", ensemble=True)
        from mdd_forecasting.branch_model import candidate_parameters
        # Rzeczywiste dwie rodziny, mały grid do testu, bez zależności od zwycięzcy walidacji.
        parameters = [spec for spec in candidate_parameters(settings) if spec["backend"] in {"hist_gradient_boosting", "lightgbm"}]
        progress = []
        with patch("mdd_forecasting.branch_model.candidate_parameters", return_value=parameters), threadpool_limits(limits=1), redirect_stdout(StringIO()):
            bundle, reports = train_branch_models(self.energy, self.weather, settings, progress=progress.append)
        prepared = next(event for event in progress if event["etap"] == "PRZYGOTOWANIE_UCZENIA")
        completed = [event["gotowe_proby"] for event in progress if "gotowe_proby" in event]
        self.assertEqual(completed[-1], len(reports["porownanie_wariantow"]))
        self.assertLessEqual(max(completed), prepared["maks_prob"])
        members = [{"parameters": spec, "columns": bundle["models"]["POBRANIE"]["columns"], "wariant": "kalendarz"} for spec in parameters]
        from mdd_forecasting.branch_model import feature_frame
        # Sprawdzamy serializację realnie dopasowanego zespołu również, gdy pojedynczy model wygrał walidację.
        direction = "POBRANIE"
        spec = bundle["models"][direction]
        part = self.energy.loc[self.energy.kierunek_energii_norm.eq(direction)].head(100).copy()
        part["horyzont_h"], part["wiek_prognozy_h"] = 12, 0
        from mdd_forecasting.config import WEATHER_FEATURES
        for column in WEATHER_FEATURES:
            part[column] = np.nan
        features = feature_frame(part, ["BIA"], settings.timezone)
        model = ValidationEnsemble(settings, members, [.5, .5]).fit(features, part.wartosc_rzeczywista)
        spec["model"] = model
        spec["parameters"] = {"backend": "ensemble", "members": members, "weights": [.5, .5]}
        with TemporaryDirectory() as tmp, threadpool_limits(limits=1):
            save_bundle(bundle, tmp)
            restored = load_bundle(tmp)
            self.assertIn("lightgbm", restored["versions"])
            with patch.object(ValidationEnsemble, "fit", side_effect=AssertionError("fit przy odczycie")):
                a, _ = predict_branches(bundle, self.weather, self.origin)
                b, _ = predict_branches(restored, self.weather, self.origin)
        np.testing.assert_allclose(a.bilans, b.bilans, rtol=0, atol=0)
        self.assertIn("ensemble", set(reports["porownanie_wariantow"].backend))
        self.assertEqual(len(reports["rekomendacje_modelu"]), 2)

    def test_calendar_features_handle_year_boundary_without_energy_lags(self):
        from mdd_forecasting.config import WEATHER_FEATURES
        frame = pd.DataFrame({"target_time_utc": pd.to_datetime(["2024-12-31T12:00Z", "2025-01-02T12:00Z"]),
                              "horyzont_h": [24, 24], "oddzial_code": ["BIA", "BIA"], "wiek_prognozy_h": [0, 0]})
        for column in WEATHER_FEATURES:
            frame[column] = 0.
        features = feature_frame(frame, ["BIA"], "Europe/Warsaw")
        self.assertEqual(features.przed_swietem.tolist(), [1., 0.])
        self.assertEqual(features.po_swiecie.tolist(), [0., 1.])
        self.assertFalse(any("lag" in column for column in features))


class TestDetailedDiagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not hasattr(backtest_fixture.TestBacktest, "bundle"):
            backtest_fixture.TestBacktest.setUpClass()

    def sample_predictions(self):
        from mdd_forecasting.backtest import score_origin
        with threadpool_limits(limits=1):
            balance, details = score_origin(backtest_fixture.TestBacktest.bundle, backtest_fixture.TestBacktest.weather, backtest_fixture.TestBacktest.energy, backtest_fixture.TestBacktest.origin)
        return balance, details

    def test_hourly_errors_and_year_month_are_explicit_and_zero_relative_error_is_missing(self):
        balance, details = self.sample_predictions()
        old_balance, old_details = balance.copy(), details.copy()
        for part in [old_balance, old_details]:
            for column in ["target_time_utc", "forecast_origin_utc"]:
                part[column] = part[column] - pd.DateOffset(years=1)
        combined_balance = pd.concat([old_balance, balance], ignore_index=True)
        combined_details = pd.concat([old_details, details], ignore_index=True)
        reports = diagnostic_tables(combined_details, combined_balance, "Europe/Warsaw")
        self.assertEqual(set(reports["bledy_miesiace"].rok_miesiac), {"2024-02", "2025-02"})
        rows = reports["bledy_kazdej_godziny"]
        self.assertEqual(len(rows), len(combined_details) + len(combined_balance))
        self.assertTrue(rows.loc[rows.realizacja.eq(0), "blad_wzgledny"].isna().all())
        np.testing.assert_allclose(rows.blad, rows.prognoza - rows.realizacja)
        self.assertEqual(reports["bledy_godziny_lacznie"].kierunek.nunique(), 3)
        self.assertIn("bledy_wiek_pogody", reports)

    def test_weather_quality_counts_requests_once_and_does_not_claim_meteorological_accuracy(self):
        _, details = self.sample_predictions()
        modified = details.copy()
        first = modified.target_time_utc.eq(modified.target_time_utc.min())
        modified.loc[first, "temperatura"] = np.nan
        modified.loc[first, "predkosc_wiatru"] = -5.
        before = modified.copy(deep=True)
        tables = weather_quality_tables(modified, "Europe/Warsaw")
        quality = tables["jakosc_pogody_zmienne"]
        self.assertTrue(quality.n_oczekiwane.eq(72).all())
        self.assertEqual(quality.loc[quality.zmienna.eq("temperatura"), "braki"].iloc[0], 1)
        self.assertEqual(quality.loc[quality.zmienna.eq("predkosc_wiatru"), "podejrzany_zakres"].iloc[0], 1)
        self.assertTrue(tables["jakosc_pogody_wydania"].trafnosc_meteo.eq("NIEOCENIONA_BRAK_OBSERWACJI_METEO").all())
        pd.testing.assert_frame_equal(modified, before)

    def test_weather_recommendation_compares_only_candidates_from_validation(self):
        rows = pd.DataFrame([
            ["POBRANIE", "kalendarz", "hist_gradient_boosting", .2, 10, 100],
            ["POBRANIE", "temperatura", "hist_gradient_boosting", .1, 11, 100],
            ["POBRANIE", "temperatura", "lightgbm", .15, 11, 100],
        ], columns=["kierunek", "wariant", "backend", "walidacja_macro_mae_norm", "liczba_cech", "liczba_wierszy"])
        result = weather_variant_recommendations(rows)
        chosen = result.loc[result.wariant.eq("temperatura")].iloc[0]
        self.assertEqual(chosen.najlepszy_backend, "hist_gradient_boosting")
        self.assertEqual(chosen.poprawa_vs_kalendarz, .5)
        self.assertEqual(chosen.ranking_walidacji, 1)


if __name__ == "__main__":
    unittest.main()
