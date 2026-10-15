from contextlib import redirect_stdout
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

import numpy as np
import pandas as pd

from mdd_forecasting.config import ENERGY_COLUMNS
from mdd_forecasting.energy_time_diagnostics import (
    diagnose_energy_hours, diagnostic_launch, main, write_hour_diagnostics,
)


def energy_rows(date, hours, *, client="Test Łódź", direction=1, revision=4, value=10):
    return [["ZŁT", direction, "G", client, "dowolny opis biernej", date, revision, hour, value] for hour in hours]


def raw_frame(rows):
    frame = pd.DataFrame(rows, columns=ENERGY_COLUMNS)
    frame.insert(0, "source_row", range(2, len(frame) + 2))
    frame.insert(0, "source_sheet", "dane_po_transpozycji")
    return frame


class TestEnergyHourAudit(unittest.TestCase):
    def test_spring_sequential_pattern_and_two_different_utc_interpretations(self):
        result = diagnose_energy_hours(raw_frame(energy_rows("2024-03-31", range(1, 24))))
        pattern = result.tables["wzorce_serii.csv"].iloc[0]
        self.assertEqual(pattern.sekwencyjne, "PELNY_WZORZEC")
        self.assertEqual(pattern.zegarowe_25, "NIEZGODNY")
        comparison = result.tables["porownanie_utc.csv"].set_index("H")
        self.assertEqual(comparison.loc[3, "sekwencyjne_UTC"], pd.Timestamp("2024-03-31T01:00Z"))
        self.assertTrue(pd.isna(comparison.loc[3, "zegarowe_25_UTC"]))
        self.assertTrue(pd.isna(comparison.loc[24, "sekwencyjne_UTC"]))
        self.assertEqual(comparison.loc[24, "zegarowe_25_UTC"], pd.Timestamp("2024-03-31T21:00Z"))
        self.assertIn("H=3: rekordy=1, zera=0, niezerowe_skończone=1", result.summary)
        self.assertIn("są wiosenne pełne zestawy 1–23", result.summary)

    def test_spring_clock_pattern_skips_three_and_keeps_twenty_four(self):
        result = diagnose_energy_hours(raw_frame(energy_rows("2025-03-30", [1, 2, *range(4, 25)])))
        pattern = result.tables["wzorce_serii.csv"].iloc[0]
        self.assertEqual(pattern.sekwencyjne, "NIEZGODNY")
        self.assertEqual(pattern.zegarowe_25, "PELNY_WZORZEC")
        self.assertIn("są wiosenne pełne zestawy 1,2,4…24", result.summary)

    def test_conflicting_or_invalid_series_do_not_produce_a_clean_verdict(self):
        variants = [energy_rows("2024-03-31", [3], value=11),
                    energy_rows("2024-03-31", [3], value=None)]
        for extra in variants:
            with self.subTest(extra=extra):
                result = diagnose_energy_hours(raw_frame(energy_rows("2024-03-31", range(1, 24)) + extra))
                self.assertIn("brak rozstrzygającego pełnego", result.summary)
        invalid = diagnose_energy_hours(raw_frame(energy_rows("2024-03-31", range(1, 24), direction=2)))
        self.assertEqual(invalid.tables["wzorce_serii.csv"].sekwencyjne.tolist(), ["NIEPOPRAWNY_KLUCZ"])
        self.assertIn("brak rozstrzygającego pełnego", invalid.summary)

    def test_mixed_spring_patterns_are_reported_as_mixed(self):
        rows = energy_rows("2024-03-31", range(1, 24))
        rows += energy_rows("2024-03-31", [1, 2, *range(4, 25)], client="Druga seria")
        result = diagnose_energy_hours(raw_frame(rows))
        self.assertIn("wiosną występują OBA pełne wzorce", result.summary)

    def test_autumn_labels_and_source_order_do_not_resolve_hour_25(self):
        for hours in [range(1, 26), list(reversed(range(1, 26)))]:
            with self.subTest(hours=hours):
                result = diagnose_energy_hours(raw_frame(energy_rows("2024-10-27", hours)))
                pattern = result.tables["wzorce_serii.csv"].iloc[0]
                self.assertEqual(pattern.sekwencyjne, "PELNY_WZORZEC")
                self.assertEqual(pattern.zegarowe_25, "PELNY_WZORZEC")
                comparison = result.tables["porownanie_utc.csv"].set_index("H")
                self.assertEqual(comparison.loc[25, "sekwencyjne_lokalnie"], "2024-10-27T23:00:00+01:00")
                self.assertEqual(comparison.loc[25, "zegarowe_25_lokalnie"], "2024-10-27T02:00:00+01:00")
                self.assertIn("Jesienne etykiety 1–25 pasują do OBU wariantów", result.summary)
                self.assertIn("Brak rekordów z wiosennej zmiany czasu", result.summary)

    def test_different_mdd_directions_and_revisions_do_not_create_fake_full_day(self):
        variants = [dict(client="Inny MDD"), dict(direction=-1), dict(revision=3)]
        for variant in variants:
            with self.subTest(variant=variant):
                rows = energy_rows("2024-03-31", range(1, 12))
                rows += energy_rows("2024-03-31", range(12, 24), **variant)
                patterns = diagnose_energy_hours(raw_frame(rows)).tables["wzorce_serii.csv"]
                self.assertEqual(len(patterns), 2)
                self.assertFalse(patterns.sekwencyjne.eq("PELNY_WZORZEC").any())
                self.assertTrue(patterns.sekwencyjne.eq("NIEPELNY_ZGODNY").all())

    def test_zeros_missing_values_and_24_label_spring_are_not_reinterpreted(self):
        rows = energy_rows("2024-03-31", range(1, 25))
        rows[2][-1] = 0
        rows[3][-1] = None
        result = diagnose_energy_hours(raw_frame(rows))
        pattern = result.tables["wzorce_serii.csv"].iloc[0]
        self.assertEqual(pattern.sekwencyjne, "NIEZGODNY")
        self.assertEqual(pattern.zegarowe_25, "NIEZGODNY")
        self.assertEqual(pattern.zera_I, 1)
        self.assertEqual(pattern.problemy_I, 1)
        self.assertEqual(len(result.tables["rekordy_zmiany_czasu.csv"]), 24)
        self.assertIn("H=3: rekordy=1, zera=1, niezerowe_skończone=0", result.summary)

    def test_duplicates_and_conflicting_values_are_counted_without_selection(self):
        rows = energy_rows("2024-03-31", range(1, 24))
        rows += energy_rows("2024-03-31", [3], value=10)
        rows += energy_rows("2024-03-31", [3], value=11)
        raw = raw_frame(rows)
        original = raw.copy(deep=True)
        result = diagnose_energy_hours(raw)
        pattern = result.tables["wzorce_serii.csv"].iloc[0]
        self.assertEqual(pattern.rekordy, 25)
        self.assertEqual(pattern.unikalne_H, 23)
        self.assertEqual(pattern.konflikty_I_w_tej_samej_G, 1)
        self.assertEqual(len(result.tables["rekordy_zmiany_czasu.csv"]), 25)
        pd.testing.assert_frame_equal(raw, original)

    def test_malformed_inputs_preserve_source_fields_and_rows(self):
        rows = energy_rows("2024-03-31", ["1,0", "3abc", "1,5", 26, np.inf])
        rows += energy_rows("zła data", [3], direction=2, revision=9, value=-10)
        rows += energy_rows(None, [None], value=np.inf)
        result = diagnose_energy_hours(raw_frame(rows))
        problems = result.tables["problemy_wejscia.csv"]
        self.assertEqual(problems.source_row.tolist(), [3, 4, 5, 6, 7, 8])
        self.assertEqual(problems.godzina_handlowa.iloc[0], "3abc")
        reasons = problems.loc[problems.source_row.eq(7), "powod_diagnostyczny"].iloc[0]
        for reason in ["nierozpoznana_data_F", "B_inne_niz_1_lub_minus_1", "G_poza_0_4", "brak_lub_niepoprawne_I"]:
            self.assertIn(reason, reasons)
        patterns = result.tables["wzorce_serii.csv"]
        self.assertTrue(patterns.sekwencyjne.eq("NIEPOPRAWNE_H").all())

    def test_no_dst_empty_and_unrecognized_dates_still_produce_reports(self):
        for rows in [[], energy_rows("2024-01-01", [1]), energy_rows("zła data", [1])]:
            with self.subTest(rows=rows):
                result = diagnose_energy_hours(raw_frame(rows))
                self.assertTrue(result.tables["porownanie_utc.csv"].empty)
                self.assertTrue(result.tables["wzorce_serii.csv"].empty)
                self.assertIn("Brak rekordów z wiosennej zmiany czasu", result.summary)

    def test_missing_transition_dates_are_visible_between_normal_days(self):
        rows = energy_rows("2024-03-30", [1]) + energy_rows("2024-10-28", [1])
        result = diagnose_energy_hours(raw_frame(rows))
        self.assertIn("2024-03-31 WIOSNA: 23 rzeczywiste godziny; rekordy=0", result.summary)
        self.assertIn("2024-10-27 JESIEN: 25 rzeczywiste godziny; rekordy=0", result.summary)
        self.assertTrue(result.tables["rekordy_zmiany_czasu.csv"].empty)

    def test_cli_cp1250_export_round_trip_keeps_source_and_does_not_train(self):
        rows = energy_rows("2024-03-31", range(1, 24))
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "dane_po_transpozycji.csv"
            directory = Path(tmp) / "raporty"
            pd.DataFrame(rows, columns=ENERGY_COLUMNS).to_csv(source, sep=";", decimal=",", index=False, encoding="cp1250")
            before = sha256(source.read_bytes()).hexdigest()
            with patch("mdd_forecasting.branch_cli.train_main", side_effect=AssertionError("Nie trenuj")), patch(
                "mdd_forecasting.branch_database.query_branch_weather_sql", side_effect=AssertionError("Bez SQL"),
            ), redirect_stdout(StringIO()):
                self.assertEqual(main(["--energy", str(source), "--output-dir", str(directory)]), 0)
            self.assertEqual(sha256(source.read_bytes()).hexdigest(), before)
            summary = (directory / "podsumowanie_godzin.txt").read_text(encoding="utf-8-sig")
            self.assertNotIn("Test Łódź", summary)
            self.assertNotIn(str(source), summary)
            details = pd.read_csv(directory / "rekordy_zmiany_czasu.csv", sep=";")
            self.assertEqual(details.klient_nazwa.unique().tolist(), ["Test Łódź"])
            self.assertEqual(details.oddzial_code.unique().tolist(), ["ZŁT"])
            self.assertEqual(details.source_row.tolist(), list(range(2, 25)))
            self.assertEqual(len(list(directory.iterdir())), 6)
            self.assertTrue((directory / "rekordy_zmiany_czasu.csv").read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_output_cannot_overwrite_energy_export(self):
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            source = Path(tmp) / "rekordy_zmiany_czasu.csv"
            pd.DataFrame(energy_rows("2024-03-31", [3]), columns=ENERGY_COLUMNS).to_csv(source, index=False)
            before = source.read_bytes()
            with self.assertRaisesRegex(ValueError, "nadpisanie"):
                write_hour_diagnostics(source, tmp)
            self.assertEqual(source.read_bytes(), before)

    def test_excel_sheet_selection_and_original_hour_are_preserved(self):
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()):
            source = Path(tmp) / "energia.xlsx"
            with pd.ExcelWriter(source, engine="openpyxl") as writer:
                pd.DataFrame(energy_rows("2024-03-31", ["3,0"]), columns=ENERGY_COLUMNS).to_excel(writer, sheet_name="Dane_1", index=False)
                pd.DataFrame({"opis": ["To nie są pomiary"]}).to_excel(writer, sheet_name="Opis", index=False)
            directory = Path(tmp) / "wynik"
            write_hour_diagnostics(source, directory)
            details = pd.read_csv(directory / "rekordy_zmiany_czasu.csv", sep=";", decimal=",", dtype={"godzina_handlowa": str})
            self.assertEqual(details.source_sheet.tolist(), ["Dane_1"])
            self.assertEqual(details.source_row.tolist(), [2])
            self.assertEqual(details.godzina_handlowa.tolist(), ["3,0"])
            self.assertEqual(details.godzina_odczytana.tolist(), [3.0])

    def test_vscode_launcher_reuses_local_training_paths_even_outside_repo_cwd(self):
        with patch("uruchom_trening_bilansow.SCIEZKA_ENERGII", "energia.csv"), patch(
            "uruchom_trening_bilansow.FOLDER_MODELU", "Wynik",
        ), patch("mdd_forecasting.energy_time_diagnostics.write_hour_diagnostics") as write:
            self.assertEqual(diagnostic_launch(), 0)
        from mdd_forecasting.branch_launcher import ROOT
        self.assertEqual(Path(write.call_args.args[0]), ROOT / "energia.csv")
        self.assertEqual(Path(write.call_args.args[1]), ROOT / "Wynik" / "diagnostyka_godzin")


if __name__ == "__main__":
    unittest.main()
