from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import re
import unittest
import warnings

import pandas as pd

from mdd_forecasting.branch_data import BRANCH_ALIASES, BRANCHES, load_branch_energy, load_prediction_measurements
from mdd_forecasting.branch_database import DEFAULT_BRANCH_SQL
from mdd_forecasting.branch_launcher import configured_launch
from mdd_forecasting.config import DEFAULT_MAPPING_PATH, ENERGY_COLUMNS, PipelineConfig
from mdd_forecasting.io import _ascii_token, load_branch_mapping


class TestBranchMapping(unittest.TestCase):
    def load_quietly(self, path):
        with redirect_stdout(StringIO()), warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return load_branch_mapping(path)

    def test_supported_encodings_and_excel_separators_preserve_polish_text(self):
        mapping_text = "oddzial_code,punkt,status,uwaga\nZŁT,Łódź,active,Żółć\nŁZE,Łódź,active,miasto\n"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapowanie.csv"
            for encoding in ["utf-8", "utf-8-sig", "cp1250", "utf-16", "utf-16-be"]:
                for separator in [",", ";", "\t"]:
                    with self.subTest(encoding=encoding, separator=separator):
                        text = mapping_text.replace(",", separator)
                        data = text.encode(encoding)
                        if encoding == "utf-16-be":
                            data = b"\xfe\xff" + data
                        path.write_bytes(data)
                        mapping, table = self.load_quietly(path)
                        self.assertEqual(mapping, {"zlt": "Łódź", "lze": "Łódź"})
                        self.assertEqual(table.oddzial_code.tolist(), ["ZŁT", "ŁZE"])
                        self.assertEqual(table.uwaga.iloc[0], "Żółć")

    def test_cp1250_fallback_explains_encoding_and_resolved_path(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapowanie.csv"
            path.write_bytes("oddzial_code;punkt;status\nZŁT;Łódź;active\n".encode("cp1250"))
            self.assertIn(b"\xa3", path.read_bytes())
            output = StringIO()
            with redirect_stdout(output), self.assertWarnsRegex(UserWarning, "Windows-1250"):
                mapping, table = load_branch_mapping(path)
            self.assertEqual(mapping["zlt"], "Łódź")
            self.assertEqual(table.attrs["mapping_path"], str(path.resolve()))
            self.assertIn(str(path.resolve()), output.getvalue())
            self.assertIn("cp1250", output.getvalue())

    def test_bad_bytes_and_truncated_utf16_are_rejected_without_lossy_fallback(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "uszkodzone.csv"
            for data in [b"oddzial_code,punkt,status\nX,\x81,active\n", b"\xff\xfeo\x00x"]:
                with self.subTest(data=data):
                    path.write_bytes(data)
                    with self.assertRaisesRegex(ValueError, "kodowanie") as raised:
                        load_branch_mapping(path)
                    self.assertIn(str(path.resolve()), str(raised.exception))
                    self.assertIn("UTF-8", str(raised.exception))

    def test_whitespace_case_and_literal_na_are_handled_as_text(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapowanie.csv"
            path.write_text(" ODDZIAL_CODE ; PUNKT ; STATUS \n ZŁT ; Łódź ; ACTIVE \nNA;NA;active\n", encoding="utf-8")
            mapping, table = self.load_quietly(path)
        self.assertEqual(mapping, {"zlt": "Łódź", "na": "NA"})
        self.assertEqual(table.status.tolist(), ["active", "active"])

    def test_invalid_mapping_has_actionable_validation(self):
        cases = [
            ("kod,punkt,status\nX,Łódź,active\n", "nie ma kolumn"),
            ("oddzial_code,punkt,status\nX,Łódź,aktywne\n", "status.*wiersze.*2"),
            ("oddzial_code,punkt,status\nX,Łódź,\n", "status.*wiersze.*2"),
            ("oddzial_code,punkt,status\n,Łódź,active\n", "Pusty kod"),
            ("oddzial_code,punkt,status\nX, ,active\n", "punkt pogody"),
            ("oddzial_code,punkt,status\nX,Łódź,inactive\n", "Brak aktywnych"),
            ("oddzial_code,punkt,status\n", "Brak aktywnych"),
            ("oddzial_code,punkt,punkt,status\nX,Łódź,Łódź,active\n", "Powtórzone nagłówki"),
            ("oddzial_code, Punkt ,punkt,status\nX,Łódź,Łódź,active\n", "Powtórzone nagłówki"),
            ("oddzial_code,punkt,status\nZŁT,Łódź,active,active\n", "Nie można wczytać mapowania"),
            ("", "Nie można wczytać mapowania"),
        ]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapowanie.csv"
            for text, expected in cases:
                with self.subTest(text=text):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, expected) as raised:
                        load_branch_mapping(path)
                    self.assertIn(str(path.resolve()), str(raised.exception))

    def test_equivalent_codes_cannot_have_conflicting_active_points(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapowanie.csv"
            path.write_text("oddzial_code,punkt,status\nŁZE,Łódź,active\nLZE,Warszawa,active\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Sprzeczne aktywne"):
                load_branch_mapping(path)
            path.write_text("oddzial_code,punkt,status\nŁZE,Łódź,active\nLZE,Łódź,active\n", encoding="utf-8")
            mapping, table = self.load_quietly(path)
            self.assertEqual(mapping, {"lze": "Łódź"})
            self.assertEqual(len(table), 2)

    def test_default_mapping_covers_branches_aliases_and_sql_points(self):
        mapping, table = self.load_quietly(DEFAULT_MAPPING_PATH)
        self.assertEqual(len(BRANCHES), 8)
        for code in BRANCHES:
            self.assertIn(_ascii_token(code), mapping)
        for alias, code in BRANCH_ALIASES.items():
            self.assertEqual(mapping[_ascii_token(alias)], mapping[_ascii_token(code)])
        self.assertNotIn("ska", mapping)
        sql_points = set(re.findall(r"N'([^']+)'", DEFAULT_BRANCH_SQL.read_text(encoding="utf-8")))
        self.assertTrue(set(mapping.values()).issubset(sql_points))
        self.assertEqual(table.loc[table.oddzial_code.eq("ZŁT"), "punkt"].iloc[0], "Łódź")

    def test_default_mapping_is_independent_of_working_directory(self):
        original = Path.cwd()
        with TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                mapping, table = self.load_quietly(PipelineConfig().mapping_path)
            finally:
                os.chdir(original)
        self.assertEqual(mapping["zlt"], "Łódź")
        self.assertEqual(table.attrs["mapping_path"], str(DEFAULT_MAPPING_PATH.resolve()))

    def test_missing_mapping_names_actual_path(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "brak.csv"
            with self.assertRaisesRegex(FileNotFoundError, "SCIEZKA_MAPOWANIA") as raised:
                load_branch_mapping(path)
            self.assertIn(str(path.resolve()), str(raised.exception))

    def test_windows_mapping_and_both_lodz_branches_load_without_merging_balances(self):
        rows = [[code, 1, "G", "MDD testowe", "czynne pobranie", "2025-01-01", 4, 1, value]
                for code, value in [("ZŁT", 10), ("ZLT", 10), ("ŁZE", 30)]]
        with TemporaryDirectory() as tmp:
            energy_path = Path(tmp) / "energia.csv"
            mapping_path = Path(tmp) / "mapowanie.csv"
            pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_csv(energy_path, sep=";", encoding="cp1250", index=False)
            mapping_path.write_bytes("oddzial_code;punkt;status\nZŁT;Łódź;active\nŁZE;Łódź;active\n".encode("cp1250"))
            with redirect_stdout(StringIO()), self.assertWarnsRegex(UserWarning, "Windows-1250"):
                hourly, _, _ = load_branch_energy(energy_path, replace(PipelineConfig(), mapping_path=mapping_path))
        self.assertEqual(set(hourly.oddzial_code), {"ZEUD", "ZEUE"})
        self.assertEqual(set(hourly.punkt), {"Łódź"})
        values = hourly.set_index("oddzial_code").wartosc_rzeczywista
        self.assertEqual(values["ZEUD"], 10)  # Ten sam pomiar pod dwoma aliasami nie jest sumowany.
        self.assertEqual(values["ZEUE"], 30)

    def test_prediction_audit_resolves_lodz_aliases_from_saved_mapping(self):
        bundle = {"branches": pd.DataFrame({"oddzial_code": ["ZEUD", "ZEUE"], "punkt": ["Łódź", "Łódź"]}),
                  "settings": {"timezone": "Europe/Warsaw"}}
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "energia.csv"
            rows = [[code, 1, "G", "Test", "czynne pobranie", "2025-01-01", 4, 1, 10]
                    for code in ["ZŁT", "ZLT", "ŁZE", "LZE"]]
            pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_csv(path, index=False)
            with patch("mdd_forecasting.branch_data.load_branch_mapping", side_effect=AssertionError("Nie czytaj nowego CSV")):
                measurements = load_prediction_measurements(path, bundle)
        self.assertEqual(set(measurements.oddzial_code), {"ZEUD", "ZEUE"})
        self.assertEqual(set(measurements.punkt), {"Łódź"})
        self.assertEqual(len(measurements), 2)

    def test_unknown_branch_diagnostic_names_custom_file_and_source_row(self):
        with TemporaryDirectory() as tmp:
            energy_path = Path(tmp) / "energia.csv"
            mapping_path = Path(tmp) / "mapowanie.csv"
            mapping_path.write_text("oddzial_code,punkt,status\nBIA,Białystok,active\n", encoding="utf-8")
            rows = [["ZŁT", 1, "G", "Test", "czynne pobranie", "2025-01-01", 4, 1, 10]]
            pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_csv(energy_path, index=False)
            with redirect_stdout(StringIO()), self.assertRaisesRegex(ValueError, "Brak mapowania") as raised:
                load_branch_energy(energy_path, replace(PipelineConfig(), mapping_path=mapping_path))
            message = str(raised.exception)
            for fragment in ["ZŁT", "zlt", str(mapping_path.resolve()), "source_row", "active", "inactive"]:
                self.assertIn(fragment, message)

    def test_launcher_resolves_custom_mapping_relative_to_project(self):
        original = Path.cwd()
        with TemporaryDirectory() as tmp:
            project = Path(tmp) / "projekt"
            project.mkdir()
            (project / "energia.csv").touch()
            try:
                os.chdir(tmp)
                with patch("mdd_forecasting.branch_launcher.ROOT", project), patch(
                    "mdd_forecasting.branch_cli.train_main", return_value=0,
                ) as train:
                    self.assertEqual(configured_launch(training=True, energy="energia.csv",
                                                      mapping_file="config/mapowanie.csv"), 0)
                args = train.call_args.args[0]
                self.assertEqual(args[args.index("--mapping") + 1], str(project / "config/mapowanie.csv"))
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
