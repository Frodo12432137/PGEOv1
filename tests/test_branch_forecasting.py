from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import numpy as np
import pandas as pd

from mdd_forecasting.branch_data import (
    aggregate_branch_energy, future_requests, historical_requests,
    join_weather_asof, load_branch_energy, prepare_weather_vintages, trade_time_utc,
)
from mdd_forecasting.branch_database import query_branch_weather_sql, DEFAULT_BRANCH_SQL
from mdd_forecasting.branch_model import (
    TrainingSettings, balance_table, feature_frame, load_bundle,
    predict_branches, save_bundle, train_branch_models,
)
from mdd_forecasting.config import ENERGY_COLUMNS, PipelineConfig
from mdd_forecasting.database import SqlServerSettings
from mdd_forecasting.features import _polish_holidays
from mdd_forecasting.io import load_branch_mapping, normalize_energy


def normalized_energy(rows):
    mapping, _ = load_branch_mapping(PipelineConfig().mapping_path)
    raw = pd.DataFrame(rows, columns=ENERGY_COLUMNS)
    raw.insert(0, "source_row", np.arange(2, len(raw) + 2))
    raw.insert(0, "source_sheet", "dane_po_transpozycji")
    return normalize_energy(raw, mapping)[0]


def demo_data(days=45):
    branch_map = {"BIA": "Białystok", "LUB": "Lublin", "RZE": "Rzeszów", "SKO": "Skarżysko-Kamienna",
                  "WAW": "Warszawa", "ZAM": "Zamość", "ZEUD": "Łódź", "ZEUE": "Łódź"}
    times = pd.date_range("2025-01-01T00:00:00Z", periods=days * 24 + 73, freq="h")
    rows, wx = [], []
    for number, (code, city) in enumerate(branch_map.items()):
        for no, ts in enumerate(times):
            local = ts.tz_convert("Europe/Warsaw").tz_localize(None)
            sun = max(0, np.sin(np.pi * (local.hour - 6) / 12)) * 400
            temp = 8 + 5 * np.sin(no / 37)
            if no < days * 24:
                for sign, label, value in [(1, "czynne pobranie", 1000 + 70 * number + (18 - temp) * 40 + 100 * np.cos(no / 24)),
                                           (-1, "czynne oddanie", sun * (1 + number / 10))]:
                    rows.append([code, sign, "G", f"KLIENT_{code}", label, local.normalize(), 4, local.hour + 1, value])
            if city != "Łódź" or code == "ZEUD":
                for lead in [96, 24, 6]:
                    issued = ts - pd.Timedelta(hours=lead)
                    wx.append({"punkt": city, "dataGodzinaCET": local, "dataGodzinaUTC": ts,
                               "czasDanychZrodlaUTC": issued,
                               "czasDanychZrodlaCET": issued.tz_convert("Europe/Warsaw").tz_localize(None),
                               "temperatura": temp, "predkoscWiatru": 3.0, "kierunekWiatru": 350.0,
                               "zachmurzenie": 0.2, "calkowitePromieniowanieSloneczneGodzinowe": sun,
                               "bezposredniePromieniowanieSloneczneGodzinowe": sun * 0.8,
                               "warstwaSniegu": 0.0})
    raw = pd.DataFrame(rows, columns=ENERGY_COLUMNS)
    energy, _ = aggregate_branch_energy(normalized_energy(rows))
    weather_raw = pd.DataFrame(wx)
    weather = prepare_weather_vintages(weather_raw, PipelineConfig())
    return raw, energy, weather_raw, weather, times[days * 24]


class TestBranchData(unittest.TestCase):
    def test_windows_cp1250_csv_preserves_polish_branch_and_mdd(self):
        rows = [["ŁZE", 1, "G", "Klient Łódź", "czynne pobranie", "2025-01-01", 4, 1, 10]]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "energia.csv"
            pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_csv(path, sep=";", index=False, encoding="cp1250")
            energy, _, _ = load_branch_energy(path, PipelineConfig())
        self.assertEqual(energy.oddzial_code.tolist(), ["ZEUE"])
        self.assertEqual(energy.punkt.tolist(), ["Łódź"])

    def test_explicit_weather_utc_overrides_inconsistent_trade_hour(self):
        raw = pd.DataFrame({"punkt": ["Białystok"], "dataCET": ["2025-01-01"],
                            "godzinaHandlowa25": [2], "dataGodzinaCET": ["2025-01-01 02:00"],
                            "dataGodzinaUTC": ["2025-01-01T01:00Z"],
                            "czasDanychZrodlaCET": ["2024-12-31 12:00"]})
        with self.assertWarnsRegex(UserWarning, "Używam czasów UTC"):
            weather = prepare_weather_vintages(raw, PipelineConfig())
        self.assertEqual(weather.weather_valid_time_utc.iloc[0], pd.Timestamp("2025-01-01T01:00Z"))
        self.assertIn("ROZBIEZNOSC_KLUCZA_HANDLOWEGO", weather.kontrola_czasu_pogody.iloc[0])

    def test_best_revision_once_per_measurement_and_signed_balance(self):
        rows = [
            ["BIA", 1, "", "K1", "czynne pobranie", "2025-01-01", 1, 1, 100],
            ["BIA", 1, "", "K1", "czynne pobranie", "2025-01-01", 4, 1, 120],
            ["BIA", 1, "", "K1", "czynne pobranie", "2025-01-01", 4, 1, 120],
            ["BIA", 1, "", "K2", "czynne pobranie", "2025-01-01", 2, 1, 30],
            ["BIA", -1, "", "K1", "czynne oddanie", "2025-01-01", 4, 1, 40],
        ]
        energy, quality = aggregate_branch_energy(normalized_energy(rows))
        values = energy.set_index("kierunek_energii_norm")["wartosc_rzeczywista"]
        self.assertEqual(values["POBRANIE"], 150)
        self.assertEqual(values["ODDANIE"], 40)
        self.assertEqual(quality.loc[quality.kontrola.eq("odrzucone_korekty_i_duplikaty"), "liczba"].iloc[0], 2)
        energy["forecast_origin_utc"] = energy["target_time_utc"] - pd.Timedelta(hours=24)
        energy["horyzont_h"] = 24
        self.assertEqual(balance_table(energy, ["wartosc_rzeczywista"]).iloc[0]["bilans_wartosc_rzeczywista"], 110)

    def test_same_version_conflict_is_not_summed(self):
        row = ["BIA", 1, "G", "K", "czynne pobranie", "2025-01-01", 4, 1, 10]
        with self.assertRaisesRegex(ValueError, "różne wartości"):
            aggregate_branch_energy(normalized_energy([row, [*row[:-1], 20]]))

    def test_missing_client_value_does_not_become_partial_total(self):
        rows = [["BIA", 1, "G", "K1", "czynne pobranie", "2025-01-01", 4, 1, 10],
                ["BIA", 1, "G", "K2", "czynne pobranie", "2025-01-01", 4, 1, None]]
        energy, _ = aggregate_branch_energy(normalized_energy(rows))
        self.assertTrue(pd.isna(energy.iloc[0]["wartosc_rzeczywista"]))

    def test_column_b_overrides_opposite_e_and_unknown_branch_is_rejected(self):
        row = ["BIA", -1, "G", "K", "czynne pobranie", "2025-01-01", 4, 1, 10]
        hourly, _ = aggregate_branch_energy(normalized_energy([row]))
        self.assertEqual(hourly.kierunek_energii_norm.tolist(), ["ODDANIE"])
        row[0] = "BRAK_MAPOWANIA"
        with self.assertRaisesRegex(ValueError, "Brak mapowania"):
            aggregate_branch_energy(normalized_energy([row]))

    def test_column_b_numeric_formats_determine_direction_without_e(self):
        for code, expected in [(1, "POBRANIE"), ("1,0", "POBRANIE"), ("+1", "POBRANIE"),
                               (-1.0, "ODDANIE"), ("-1,0", "ODDANIE")]:
            with self.subTest(code=code):
                frame = normalized_energy([["BIA", code, "G", "K", None, "2025-01-01", 4, 1, 10]])
                self.assertEqual(frame.direction_source.tolist(), ["kolumna_B"])
                hourly, _ = aggregate_branch_energy(frame)
                self.assertEqual(hourly.kierunek_energii_norm.tolist(), [expected])

    def test_invalid_column_b_has_no_e_fallback_and_reports_source_record(self):
        for code in [None, "błędny kod", 0, 2, -2, 0.5, np.inf, -np.inf]:
            with self.subTest(code=code):
                frame = normalized_energy([["BIA", code, "G", "K", "czynne pobranie", "2025-01-01", 4, 1, 10]])
                with self.assertRaisesRegex(ValueError, "kolumnie B") as raised:
                    aggregate_branch_energy(frame)
                message = str(raised.exception)
                for part in ["source_row", "source_sheet", "dane_po_transpozycji", "kierunek_code_zrodlowy"]:
                    self.assertIn(part, message)
                if isinstance(code, str):
                    self.assertIn(code, message)

    def test_different_e_descriptions_do_not_duplicate_or_hide_conflicting_measurements(self):
        rows = [["BIA", 1, "G", "K", label, "2025-01-01", 4, 1, 10]
                for label in ["czynne pobranie", "energia bierna oddana"]]
        hourly, quality = aggregate_branch_energy(normalized_energy(rows))
        self.assertEqual(hourly.wartosc_rzeczywista.tolist(), [10])
        self.assertEqual(quality.loc[quality.kontrola.eq("odrzucone_korekty_i_duplikaty"), "liczba"].iloc[0], 1)
        rows[1][-1] = 12
        with self.assertRaisesRegex(ValueError, "różne wartości") as raised:
            aggregate_branch_energy(normalized_energy(rows))
        message = str(raised.exception)
        self.assertIn("source_row", message)
        self.assertIn("energia bierna oddana", message)

    def test_xlsx_column_b_defines_both_directions_despite_descriptions(self):
        rows = [["ZŁT", 1, "G", "K", "energia bierna oddana", "2025-01-01", 4, 1, 10],
                ["ZŁT", -1, "G", "K", "czynne pobranie", "2025-01-01", 4, 1, 4]]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "energia.xlsx"
            pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_excel(path, index=False, sheet_name="dane_po_transpozycji")
            energy, quality, _ = load_branch_energy(path, PipelineConfig())
        values = energy.set_index("kierunek_energii_norm").wartosc_rzeczywista
        self.assertEqual(values["POBRANIE"] - values["ODDANIE"], 6)
        self.assertEqual(set(energy.oddzial_code), {"ZEUD"})
        self.assertEqual(quality.loc[quality.kontrola.eq("niepoprawny_kierunek_B"), "liczba"].iloc[0], 0)

    def test_trade_hours_dst_have_distinct_utc(self):
        result = trade_time_utc(pd.Series(pd.to_datetime(["2025-10-26"] * 2)), pd.Series([3, 25]), "Europe/Warsaw")
        self.assertEqual(result.iloc[0], pd.Timestamp("2025-10-26T00:00:00Z"))
        self.assertEqual(result.iloc[1], pd.Timestamp("2025-10-26T01:00:00Z"))
        spring = trade_time_utc(pd.Series(pd.to_datetime(["2025-03-30"])), pd.Series([3]), "Europe/Warsaw")
        self.assertTrue(pd.isna(spring.iloc[0]))

    def test_72_real_hours_across_autumn_and_spring(self):
        branches = pd.DataFrame({"oddzial_code": ["BIA"], "punkt": ["Białystok"]})
        autumn = future_requests(branches, "2025-10-25T00:00:00Z", 72, "Europe/Warsaw")
        spring = future_requests(branches, "2025-03-29T00:00:00Z", 72, "Europe/Warsaw")
        self.assertEqual(len(autumn), 72)
        self.assertEqual(len(spring), 72)
        self.assertEqual(int(autumn["godzina_handlowa"].eq(25).sum()), 1)
        self.assertFalse(spring.loc[spring["doba_handlowa"].eq(pd.Timestamp("2025-03-30")), "godzina_handlowa"].eq(3).any())

    def test_asof_is_frozen_for_whole_horizon(self):
        origin = pd.Timestamp("2025-01-01T10:00:00Z")
        valid = origin + pd.Timedelta(hours=60)
        weather_raw = pd.DataFrame({"punkt": ["Łódź"] * 3,
            "dataGodzinaCET": [valid.tz_convert("Europe/Warsaw").tz_localize(None)] * 3,
            "dataGodzinaUTC": [valid] * 3,
            "czasDanychZrodlaUTC": [origin - pd.Timedelta(hours=1), origin + pd.Timedelta(hours=1), valid],
            "czasDanychZrodlaCET": [(origin + pd.Timedelta(hours=i)).tz_convert("Europe/Warsaw").tz_localize(None) for i in [-1, 1, 60]],
            "temperatura": [5, 99, 999]})
        weather = prepare_weather_vintages(weather_raw, PipelineConfig())
        self.assertEqual(len(weather), 3)
        requests = pd.DataFrame({"oddzial_code": ["ZEUD", "ZEUE"], "punkt": ["Łódź"] * 2,
                                 "target_time_utc": [valid] * 2, "forecast_origin_utc": [origin] * 2})
        joined = join_weather_asof(requests, weather)
        self.assertEqual(joined.temperatura.tolist(), [5, 5])
        self.assertTrue(joined["weather_issue_time_utc"].le(joined["forecast_origin_utc"]).all())
        later = requests.copy()
        later["forecast_origin_utc"] += pd.Timedelta(hours=2)
        self.assertEqual(join_weather_asof(later, weather).temperatura.tolist(), [99, 99])

    def test_missing_weather_stays_missing(self):
        origin = pd.Timestamp("2025-01-01T10:00:00Z")
        raw = pd.DataFrame({"punkt": ["Białystok"], "dataGodzinaCET": ["2025-01-02 01:00"],
                            "czasDanychZrodlaCET": ["2025-01-01 12:00"], "temperatura": [99]})
        weather = prepare_weather_vintages(raw, PipelineConfig())
        req = pd.DataFrame({"punkt": ["Białystok"], "target_time_utc": [pd.Timestamp("2025-01-02T00:00Z")],
                            "forecast_origin_utc": [origin]})
        joined = join_weather_asof(req, weather)
        self.assertFalse(joined.pogoda_dopasowana.iloc[0])
        self.assertTrue(pd.isna(joined.temperatura.iloc[0]))

    def test_sql_parameters_and_connection_cleanup(self):
        from unittest.mock import Mock
        connection = SimpleNamespace(close=Mock(), timeout=0)
        fake = SimpleNamespace(drivers=lambda: ["ODBC Driver 17 for SQL Server"], connect=lambda *a, **k: connection)
        with patch.dict("sys.modules", {"pyodbc": fake}), patch("mdd_forecasting.branch_database.pd.read_sql_query", return_value=pd.DataFrame()) as query:
            query_branch_weather_sql(DEFAULT_BRANCH_SQL, SqlServerSettings(), "2025-01-01", "2025-01-02", "2025-01-01T10:00Z")
        self.assertEqual(len(query.call_args.kwargs["params"]), 5)
        self.assertEqual(query.call_args.kwargs["params"][-2:], [None, None])
        connection.close.assert_called_once()
        self.assertIn("Trusted_Connection=yes", SqlServerSettings().connection_string())

    def test_sql_connection_is_closed_on_error(self):
        from unittest.mock import Mock
        connection = SimpleNamespace(close=Mock(), timeout=0)
        fake = SimpleNamespace(drivers=lambda: ["ODBC Driver 17 for SQL Server"], connect=lambda *a, **k: connection)
        with patch.dict("sys.modules", {"pyodbc": fake}), patch("mdd_forecasting.branch_database.pd.read_sql_query", side_effect=RuntimeError("SQL")):
            with self.assertRaisesRegex(RuntimeError, "SQL"):
                query_branch_weather_sql(DEFAULT_BRANCH_SQL, SqlServerSettings(), "2025-01-01", "2025-01-02", "2025-01-01T10:00Z")
        connection.close.assert_called_once()

    def test_christmas_eve_calendar(self):
        from datetime import date
        self.assertNotIn(date(2024, 12, 24), _polish_holidays(2024))
        self.assertIn(date(2025, 12, 24), _polish_holidays(2025))


class TestBranchModel(unittest.TestCase):
    def test_cli_train_then_multiple_predictions_keep_artifacts_unchanged(self):
        from mdd_forecasting.branch_cli import train_main, predict_main
        import hashlib
        raw, _, weather_raw, _, origin = demo_data(days=25)
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            energy_path, weather_path = directory / "energia.csv", directory / "pogoda.csv"
            raw.to_csv(energy_path, sep=";", decimal=",", index=False, encoding="utf-8-sig")
            weather_raw.to_csv(weather_path, index=False, encoding="utf-8-sig")
            model_dir = directory / "model"
            result = train_main(["--energy", str(energy_path), "--weather", str(weather_path),
                                 "--artifacts-dir", str(model_dir), "--validation-days", "4", "--test-days", "4",
                                 "--label-delay-hours", "24", "--max-iter", "8", "--skip-importance"])
            self.assertEqual(result, 0)
            model_path = model_dir / "modele_oddzialow.joblib"
            self.assertTrue((model_dir / "kontrola_czasu_pogody.csv").is_file())
            before = hashlib.sha256(model_path.read_bytes()).hexdigest()
            for i in range(2):
                args = ["--artifacts-dir", str(model_dir), "--weather", str(weather_path),
                        "--output-dir", str(directory / f"prognoza{i}"), "--as-of", origin.isoformat()]
                if i:
                    args += ["--energy", str(energy_path)]
                result = predict_main(args)
                self.assertEqual(result, 0)
                output = pd.read_csv(directory / f"prognoza{i}" / "prognoza_bilansu_72h.csv", sep=";", decimal=",")
                self.assertEqual(len(output), 8 * 72)
                self.assertTrue(output["bilans"].notna().all())
                self.assertTrue(output["status_portfela"].eq("BEZ_DLUGICH_BRAKOW_W_POMIARACH").all())
                self.assertTrue((directory / f"prognoza{i}" / "portfel_mdd.csv").exists())
                self.assertTrue((directory / f"prognoza{i}" / "kontrola_czasu_pogody.csv").is_file())
            changed = raw.copy()
            changed.loc[changed["oddzial_code"].eq("BIA"), "klient_nazwa"] = "NOWY_MDD_BIA"
            changed_path = directory / "zmieniony_portfel.csv"
            changed.to_csv(changed_path, sep=";", decimal=",", index=False, encoding="utf-8-sig")
            result = predict_main(["--artifacts-dir", str(model_dir), "--weather", str(weather_path),
                                   "--output-dir", str(directory / "prognoza_zmiana"), "--as-of", origin.isoformat(),
                                   "--energy", str(changed_path)])
            self.assertEqual(result, 0)
            output = pd.read_csv(directory / "prognoza_zmiana" / "prognoza_bilansu_72h.csv", sep=";", decimal=",")
            self.assertTrue(output.loc[output.oddzial_code.eq("BIA"), "status_portfela"].eq("PORTFEL_DO_WERYFIKACJI").all())
            self.assertTrue(output.loc[output.oddzial_code.ne("BIA"), "status_portfela"].eq("BEZ_DLUGICH_BRAKOW_W_POMIARACH").all())
            self.assertEqual(hashlib.sha256(model_path.read_bytes()).hexdigest(), before)

    def test_training_saved_models_and_prediction_without_fit(self):
        raw, energy, weather_raw, weather, origin = demo_data(days=25)
        settings = TrainingSettings(validation_days=4, test_days=4, label_delay_hours=24,
                                    max_iter=8, min_train_rows=40, importance_rows=20)
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=2):
            bundle, reports = train_branch_models(energy, weather, settings)
        self.assertEqual(len(bundle["branches"]), 8)
        self.assertTrue(set(reports["metryki_test"]["kierunek"]) == {"POBRANIE", "ODDANIE", "BILANS"})
        self.assertFalse(reports["waznosc_pogody"].empty)
        balance = reports["backtest_bilans"]
        np.testing.assert_allclose(balance["bilans_wartosc_przewidywana"],
                                   balance["pobranie_wartosc_przewidywana"] - balance["oddanie_wartosc_przewidywana"])
        with TemporaryDirectory() as tmp:
            save_bundle(bundle, tmp)
            restored = load_bundle(tmp)
            from sklearn.ensemble import HistGradientBoostingRegressor
            with patch.object(HistGradientBoostingRegressor, "fit", side_effect=AssertionError("Predykcja nie może trenować")), threadpool_limits(limits=2):
                prediction, detail = predict_branches(restored, weather, origin, 72)
            self.assertEqual(len(prediction), 8 * 72)
            self.assertTrue(prediction.bilans.notna().all())
            self.assertTrue(detail["weather_issue_time_utc"].le(detail["forecast_origin_utc"]).all())
            self.assertEqual(detail["forecast_origin_utc"].nunique(), 1)
            self.assertTrue((Path(tmp) / "manifest_oddzialy.json").exists())
            with self.assertRaisesRegex(ValueError, "zakres"):
                predict_branches(restored, weather, origin, 100)
            with self.assertRaisesRegex(ValueError, "wcześniejszy"):
                predict_branches(restored, weather, origin - pd.Timedelta(days=2))

    def test_short_history_does_not_publish_unvalidated_model(self):
        _, energy, _, weather, _ = demo_data(days=5)
        with self.assertRaisesRegex(ValueError, "za krótka"):
            train_branch_models(energy, weather, TrainingSettings())


if __name__ == "__main__":
    unittest.main()
