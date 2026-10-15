from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

import pandas as pd

from mdd_forecasting.branch_data import EnergyTimeError, aggregate_branch_energy, load_branch_energy, load_prediction_measurements
from mdd_forecasting.branch_launcher import configured_launch
from mdd_forecasting.config import ENERGY_COLUMNS, PipelineConfig
from tests.test_branch_forecasting import normalized_energy


class TestEnergyInputDiagnostics(unittest.TestCase):
    def test_start_date_excludes_old_rows_before_aggregation_and_reports_count(self):
        rows = [["BIA", 1, "G", "Stary", "", "2024-03-31", 4, 3, 123],
                ["BIA", 1, "G", "Aktualny", "", "2024-07-01", 4, 3, 100]]
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            path = Path(tmp) / "energia.csv"
            pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_csv(path, index=False)
            hourly, quality, _, measurements = load_branch_energy(
                path, PipelineConfig(), return_measurements=True, start_date="2024-07-01",
            )
            self.assertEqual(hourly.wartosc_rzeczywista.tolist(), [100])
            self.assertEqual(measurements.klient_nazwa.tolist(), ["Aktualny"])
            self.assertEqual(quality.loc[quality.kontrola.eq("przed_data_startowa"), "liczba"].iloc[0], 1)
            with self.assertRaises(EnergyTimeError):
                load_branch_energy(path, PipelineConfig())

    def test_start_date_keeps_unknown_dates_and_invalid_hours_within_scope(self):
        for date in ["brak daty", "2025-03-30"]:
            with self.subTest(date=date), TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
                path = Path(tmp) / "energia.csv"
                rows = [["BIA", 1, "G", "Test", "", date, 4, 3, 10]]
                pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_csv(path, index=False)
                with self.assertRaises(EnergyTimeError) as raised:
                    load_branch_energy(path, PipelineConfig(), start_date="2024-07-01")
                self.assertEqual(raised.exception.records.doba_handlowa_zrodlowa.tolist(), [date])

    def test_prediction_audit_applies_saved_start_date_before_aggregation(self):
        bundle = {"branches": pd.DataFrame({"oddzial_code": ["BIA"], "punkt": ["Białystok"]}),
                  "settings": {"timezone": "Europe/Warsaw"}, "input_schema": {"start_date": "2024-07-01"}}
        rows = [["BIA", 1, "G", "Stary", "", "2024-03-31", 4, 3, 123],
                ["BIA", 1, "G", "Aktualny", "", "2024-07-01", 4, 3, 100]]
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            path = Path(tmp) / "energia.csv"
            pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_csv(path, index=False)
            measurements = load_prediction_measurements(path, bundle)
        self.assertEqual(measurements.klient_nazwa.tolist(), ["Aktualny"])

    def test_bad_time_reports_original_fields_and_specific_reason(self):
        cases = [
            ("brak daty", 1, "nierozpoznana lub pusta data F"),
            (None, 1, "nierozpoznana lub pusta data F"),
            ("2025-01-01", None, "H musi być liczbą całkowitą"),
            ("2025-01-01", "1,5", "H musi być liczbą całkowitą"),
            ("2025-01-01", "1abc", "H musi być liczbą całkowitą"),
            ("2025-01-01", 0, "H musi być liczbą całkowitą"),
            ("2025-01-01", 26, "H musi być liczbą całkowitą"),
            ("2025-01-01", 25, "godzina 25 poza dodatkową jesienną"),
            ("2025-03-30", 3, "lokalna godzina nie istnieje"),
        ]
        for date, hour, reason in cases:
            with self.subTest(date=date, hour=hour), redirect_stdout(StringIO()):
                frame = normalized_energy([["BIA", 1, "G", "Test", "", date, 4, hour, 10]])
                with self.assertRaisesRegex(ValueError, "Niepoprawna data/godzina energii") as raised:
                    aggregate_branch_energy(frame)
                message = str(raised.exception)
                for fragment in [reason, "source_row", "source_sheet", "doba_handlowa_zrodlowa",
                                 "godzina_handlowa_zrodlowa", "Liczba rekordów: 1"]:
                    self.assertIn(fragment, message)
                if isinstance(hour, str):
                    self.assertIn(hour, message)
                if isinstance(date, str):
                    self.assertIn(date, message)

    def test_valid_25_hour_autumn_day_preserves_all_measurements(self):
        rows = [["BIA", 1, "G", "Test", "", "2025-10-26", 4, hour, 10] for hour in range(1, 26)]
        with redirect_stdout(StringIO()):
            hourly, _ = aggregate_branch_energy(normalized_energy(rows))
        self.assertEqual(len(hourly), 25)
        self.assertEqual(hourly.target_time_utc.nunique(), 25)
        self.assertEqual(hourly.wartosc_rzeczywista.sum(), 250)

    def test_integer_hour_written_with_decimal_comma_remains_supported(self):
        with redirect_stdout(StringIO()):
            frame = normalized_energy([["BIA", 1, "G", "Test", "", "2025-01-01", 4, "1,0", 10]])
            hourly, _ = aggregate_branch_energy(frame)
        self.assertEqual(frame.godzina_handlowa_zrodlowa.iloc[0], "1,0")
        self.assertEqual(hourly.godzina_handlowa.iloc[0], 1)

    def test_launcher_routes_sql_paths_to_sql_for_training_and_prediction(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            energy = directory / "energia.csv"
            weather = directory / "pogoda.SQL"
            energy.touch()
            weather.write_text("SELECT 1", encoding="utf-8")
            for training in [True, False]:
                with self.subTest(training=training), patch(
                    "mdd_forecasting.branch_cli.train_main", return_value=0,
                ) as train, patch("mdd_forecasting.branch_cli.predict_main", return_value=0) as predict:
                    self.assertEqual(configured_launch(training, str(energy), str(directory / "model"), str(weather)), 0)
                    args = (train if training else predict).call_args.args[0]
                    self.assertEqual(args[args.index("--weather-sql") + 1], str(weather))
                    self.assertNotIn("--weather", args)

    def test_launcher_keeps_weather_exports_as_files(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            energy = directory / "energia.csv"
            weather = directory / "pogoda.csv"
            energy.touch()
            weather.touch()
            with patch("mdd_forecasting.branch_cli.train_main", return_value=0) as train:
                self.assertEqual(configured_launch(True, str(energy), str(directory / "model"), str(weather)), 0)
            args = train.call_args.args[0]
            self.assertEqual(args[args.index("--weather") + 1], str(weather))
            self.assertNotIn("--weather-sql", args)

    def test_launcher_keeps_error_log_without_old_hour_diagnostic_export(self):
        rows = [["BIA", 1, "G", f"Test {n}", "", "zła data", 4, 1, 10] for n in range(8)]
        with redirect_stdout(StringIO()):
            frame = normalized_energy(rows)
        with self.assertRaises(EnergyTimeError) as raised:
            aggregate_branch_energy(frame)
        error = raised.exception
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            energy = directory / "energia.csv"
            energy.touch()
            artifacts = directory / "model"
            with patch("mdd_forecasting.branch_cli.train_main", side_effect=error), redirect_stdout(StringIO()):
                with self.assertRaises(EnergyTimeError):
                    configured_launch(True, str(energy), str(artifacts))
            self.assertFalse((artifacts / "bledne_daty_godziny.csv").exists())
            self.assertIn("EnergyTimeError", (artifacts / "blad_bilansow.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
