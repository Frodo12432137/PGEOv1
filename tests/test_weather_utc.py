from __future__ import annotations

from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings

import pandas as pd

from mdd_forecasting.branch_cli import _weather
from mdd_forecasting.branch_data import join_weather_asof, prepare_weather_vintages
from mdd_forecasting.branch_database import DEFAULT_BRANCH_SQL, query_branch_weather_sql
from mdd_forecasting.config import PipelineConfig
from mdd_forecasting.database import SqlServerSettings
from mdd_forecasting.weather_time import WeatherTimeError, weather_time_reports


class TestWeatherUTC(unittest.TestCase):
    def prepare(self, raw):
        return prepare_weather_vintages(pd.DataFrame(raw), PipelineConfig())

    def test_utc_only_sql_naive_datetime_is_not_shifted_and_mixed_formats_work(self):
        weather = self.prepare({"punkt": ["Łódź"] * 3,
            "dataGodzinaUTC": [datetime(2025, 7, 1, 0), "2025-07-01T03:00:00+02:00", "2025-07-01T02:00Z"],
            "czasDanychZrodlaUTC": ["2025-06-30 12:00"] * 3})
        pd.testing.assert_series_equal(weather.weather_valid_time_utc.reset_index(drop=True),
            pd.Series(pd.date_range("2025-07-01T00:00Z", periods=3, freq="h"), name="weather_valid_time_utc"))
        self.assertEqual(str(weather.weather_valid_time_utc.dtype), "datetime64[ns, UTC]")
        self.assertTrue(weather.zrodlo_czasu_wydania.eq("UTC_ZRODLOWE").all())

    def test_utc_overrides_bad_local_and_trade_metadata_without_changing_input(self):
        raw = pd.DataFrame({"punkt": ["Łódź"], "dataGodzinaUTC": ["2025-07-01T00:00Z"],
            "czasDanychZrodlaUTC": ["2025-06-30T12:00Z"], "dataGodzinaCET": ["błędna data"],
            "czasDanychZrodlaCET": ["2025-06-30 12:00"], "dataCET": ["2025-07-01"], "godzinaHandlowa25": [25]})
        before = raw.copy(deep=True)
        with self.assertWarnsRegex(UserWarning, "Używam czasów UTC"):
            weather = prepare_weather_vintages(raw, PipelineConfig())
        pd.testing.assert_frame_equal(raw, before)
        row = weather.iloc[0]
        self.assertEqual(row.weather_valid_time_utc, pd.Timestamp("2025-07-01T00:00Z"))
        self.assertEqual(row.weather_issue_time_utc, pd.Timestamp("2025-06-30T12:00Z"))
        self.assertEqual(row.weather_valid_time, pd.Timestamp("2025-07-01 02:00"))
        self.assertEqual(row._raw_weather_valid_time, "błędna data")
        reports = weather_time_reports(weather)
        self.assertEqual(reports["kontrola_czasu_pogody"].rekordy.sum(), 1)
        self.assertEqual(reports["rozbieznosci_czasu_pogody"].rekord_zrodla.tolist(), [1])

    def test_spring_utc_works_even_if_local_metadata_describes_nonexistent_hour(self):
        with self.assertWarns(UserWarning):
            weather = self.prepare({"punkt": ["Łódź"], "dataGodzinaUTC": ["2024-03-31T01:00Z"],
                "czasDanychZrodlaUTC": ["2024-03-30T12:00Z"], "dataGodzinaCET": ["2024-03-31 02:00"],
                "dataCET": ["2024-03-31"], "godzinaHandlowa25": [3]})
        self.assertEqual(weather.weather_valid_time_utc.iloc[0], pd.Timestamp("2024-03-31T01:00Z"))

    def test_two_autumn_utc_hours_remain_distinct_despite_same_local_hour(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            weather = self.prepare({"punkt": ["Łódź"] * 2,
                "dataGodzinaUTC": ["2024-10-27T00:00Z", "2024-10-27T01:00Z"],
                "czasDanychZrodlaUTC": ["2024-10-26T12:00Z"] * 2,
                "dataGodzinaCET": ["2024-10-27 02:00"] * 2,
                "dataCET": ["2024-10-27"] * 2, "godzinaHandlowa25": [2, 25], "temperatura": [8, 9]})
        requests = pd.DataFrame({"punkt": ["Łódź"] * 2,
            "target_time_utc": pd.to_datetime(["2024-10-27T00:00Z", "2024-10-27T01:00Z"]),
            "forecast_origin_utc": pd.to_datetime(["2024-10-26T18:00Z"] * 2)})
        self.assertEqual(join_weather_asof(requests, weather).temperatura.tolist(), [8, 9])

    def test_missing_utc_falls_back_per_row_with_real_cet_cest_offset(self):
        weather = self.prepare({"punkt": ["Łódź"] * 3,
            "dataGodzinaUTC": ["2025-01-01T00:00Z", None, None],
            "dataGodzinaCET": ["2025-01-01 01:00", "2025-07-01 02:00", "2025-07-01T03:00:00+02:00"],
            "czasDanychZrodlaCET": ["2024-12-31 12:00", "2025-06-30 12:00", "2025-06-30T12:00Z"]})
        self.assertEqual(weather.weather_valid_time_utc.tolist(),
            list(pd.to_datetime(["2025-01-01T00:00Z", "2025-07-01T00:00Z", "2025-07-01T01:00Z"])))
        self.assertEqual(weather.zrodlo_czasu_celu.tolist(),
            ["UTC_ZRODLOWE", "CET_PRZELICZONY_NA_UTC", "CET_PRZELICZONY_NA_UTC"])

    def test_matching_autumn_local_metadata_does_not_produce_false_difference(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            weather = self.prepare({"punkt": ["Łódź"] * 2,
                "dataGodzinaUTC": ["2024-10-27T00:00Z", "2024-10-27T01:00Z"],
                "czasDanychZrodlaUTC": ["2024-10-26T12:00Z"] * 2,
                "dataGodzinaCET": ["2024-10-27 02:00"] * 2})
        self.assertTrue(weather.kontrola_czasu_pogody.eq("OK").all())

    def test_partial_trade_metadata_does_not_invalidate_other_rows(self):
        with self.assertWarns(UserWarning):
            weather = self.prepare({"punkt": ["Łódź"] * 2,
                "dataGodzinaUTC": ["2025-01-01T00:00Z", "2025-01-01T01:00Z"],
                "czasDanychZrodlaUTC": ["2024-12-31T12:00Z"] * 2,
                "dataCET": ["2025-01-01", None], "godzinaHandlowa25": [25, None]})
        reports = weather_time_reports(weather)
        self.assertEqual(reports["rozbieznosci_czasu_pogody"].rekord_zrodla.tolist(), [1])
        self.assertEqual(len(weather), 2)

    def test_local_only_ambiguous_or_nonexistent_times_are_reported_without_guessing(self):
        for stamp in ["2024-10-27 02:00", "2024-03-31 02:00"]:
            with self.subTest(stamp=stamp), self.assertRaises(WeatherTimeError) as caught:
                self.prepare({"punkt": ["Łódź"], "dataGodzinaCET": [stamp],
                              "czasDanychZrodlaCET": ["2024-03-30 12:00"]})
            self.assertEqual(caught.exception.diagnostics.rekord_zrodla.tolist(), [1])
            self.assertIn("BRAK_JEDNOZNACZNEGO_CZASU_CELU", str(caught.exception))

    def test_malformed_explicit_utc_never_silently_uses_valid_cet(self):
        for column, code in [("dataGodzinaUTC", "BLEDNY_UTC_CELU"),
                             ("czasDanychZrodlaUTC", "BLEDNY_UTC_WYDANIA")]:
            data = {"punkt": ["Łódź"], "dataGodzinaUTC": ["2025-01-01T00:00Z"],
                    "dataGodzinaCET": ["2025-01-01 01:00"], "czasDanychZrodlaUTC": ["2024-12-31T12:00Z"],
                    "czasDanychZrodlaCET": ["2024-12-31 13:00"]}
            data[column] = ["uszkodzony UTC"]
            with self.subTest(column=column), self.assertRaisesRegex(WeatherTimeError, code):
                self.prepare(data)

    def test_bad_point_missing_issue_and_subhour_target_are_not_silently_discarded(self):
        examples = [({"punkt": [None]}, "BRAK_PUNKTU"),
                    ({"czasDanychZrodlaUTC": [None]}, "BRAK_JEDNOZNACZNEGO_CZASU_WYDANIA"),
                    ({"dataGodzinaUTC": ["2025-01-01T00:00:00.001Z"]}, "CEL_POZA_PELNA_GODZINA")]
        for override, code in examples:
            with self.subTest(code=code), self.assertRaisesRegex(WeatherTimeError, code):
                self.prepare({"punkt": ["Łódź"], "dataGodzinaUTC": ["2025-01-01T00:00Z"],
                              "czasDanychZrodlaUTC": ["2024-12-31T12:00Z"], **override})

    def test_duplicate_metadata_is_audited_but_conflicting_weather_still_fails(self):
        raw = {"punkt": ["Łódź"] * 2, "dataGodzinaUTC": ["2025-01-01T00:00Z"] * 2,
               "czasDanychZrodlaUTC": ["2024-12-31T12:00Z"] * 2,
               "dataGodzinaCET": ["2025-01-01 01:00", "2025-01-01 02:00"], "temperatura": [5, 5]}
        with self.assertWarns(UserWarning):
            weather = self.prepare(raw)
        self.assertEqual(len(weather_time_reports(weather)["rozbieznosci_czasu_pogody"]), 1)
        requests = pd.DataFrame({"punkt": ["Łódź"], "target_time_utc": pd.to_datetime(["2025-01-01T00:00Z"]),
                                  "forecast_origin_utc": pd.to_datetime(["2024-12-31T18:00Z"])})
        self.assertEqual(join_weather_asof(requests, weather).temperatura.tolist(), [5])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            conflicting = self.prepare({**raw, "temperatura": [5, 9]})
            result = join_weather_asof(requests, conflicting)
        self.assertFalse(result.pogoda_dopasowana.any())
        self.assertTrue(result.temperatura.isna().all())
        self.assertTrue(result.pogoda_konflikt.all())

    def test_unused_conflicts_do_not_block_latest_issue_and_two_lodz_branches(self):
        raw = {"punkt": ["Łódź"] * 5,
               "dataGodzinaUTC": ["2025-07-02T12:00Z"] * 5,
               "czasDanychZrodlaUTC": ["2025-07-01T00:00Z"] * 2
                   + ["2025-07-01T06:00Z"] + ["2025-07-01T12:00Z"] * 2,
               "temperatura": [1, 2, 20, 90, 99],
               "wlasciciel": ["A", "B", "A", "A", "B"], "execId": [1, 2, 3, 4, 5]}
        weather = self.prepare(raw)
        requests = pd.DataFrame({"punkt": ["Łódź"] * 2, "oddzial_code": ["ZEUE", "ZEUD"],
            "target_time_utc": pd.to_datetime(["2025-07-02T12:00Z"] * 2),
            "forecast_origin_utc": pd.to_datetime(["2025-07-01T10:00Z"] * 2)})
        result = join_weather_asof(requests, weather)
        self.assertEqual(result.temperatura.tolist(), [20, 20])
        self.assertEqual(result.oddzial_code.tolist(), ["ZEUE", "ZEUD"])
        # Exactly at publication time the conflicting new version is selected:
        # no fallback to the older, convenient value.
        requests["forecast_origin_utc"] = pd.Timestamp("2025-07-01T12:00Z")
        result = join_weather_asof(requests, weather)
        self.assertEqual(len(result), 2)
        self.assertFalse(result.pogoda_dopasowana.any())
        self.assertTrue(result.temperatura.isna().all())
        self.assertTrue(result.pogoda_konflikt.all())
        self.assertTrue(result.weather_issue_time_utc.eq(pd.Timestamp("2025-07-01T12:00Z")).all())
        from mdd_forecasting.branch_cli import write_weather_time_reports
        with TemporaryDirectory() as directory:
            write_weather_time_reports(weather, directory)
            report = pd.read_csv(Path(directory) / "konflikty_wersji_pogody.csv", sep=";")
            self.assertEqual(report.weather_exec_id.tolist(), [1, 2, 4, 5])
            self.assertEqual(set(report.wlasciciel), {"A", "B"})
            self.assertTrue(report.konflikt_pola.eq("temperatura").all())

    def test_late_issues_are_kept_for_audit_and_cannot_leak_to_forecast(self):
        with self.assertWarns(UserWarning):
            weather = self.prepare({"punkt": ["Łódź"] * 3,
                "dataGodzinaUTC": ["2025-01-01T00:00Z"] * 3,
                "czasDanychZrodlaUTC": ["2024-12-31T12:00Z", "2024-12-31T19:00Z", "2025-01-01T01:00Z"],
                "temperatura": [5, 99, 999]})
        requests = pd.DataFrame({"punkt": ["Łódź"], "target_time_utc": pd.to_datetime(["2025-01-01T00:00Z"]),
                                  "forecast_origin_utc": pd.to_datetime(["2024-12-31T18:00Z"])})
        self.assertEqual(len(weather), 3)
        self.assertEqual(join_weather_asof(requests, weather).temperatura.tolist(), [5])

    def test_sql_utc_range_and_legacy_local_contract_preserve_same_instant(self):
        for month, offset in [(1, 1), (7, 2)]:
            for legacy in [False, True]:
                with self.subTest(month=month, legacy=legacy), TemporaryDirectory() as tmp:
                    start = pd.Timestamp(datetime(2025, month, 1)).tz_localize("Europe/Warsaw").tz_convert("UTC")
                    end = start + pd.Timedelta(hours=24)
                    sql = Path(tmp) / "pogoda.sql"
                    text = DEFAULT_BRANCH_SQL.read_text(encoding="utf-8")
                    if legacy:
                        text = text.replace("-- PGEO_VALID_RANGE: UTC", "")
                        text = text.replace("[dataGodzinaUTC] >= @odUTC", "[dataGodzinaCET] >= @odUTC")
                        text = text.replace("[dataGodzinaUTC] < @doUTC", "[dataGodzinaCET] < @doUTC")
                    sql.write_text(text, encoding="utf-8")
                    connection = SimpleNamespace(close=Mock())
                    fake = SimpleNamespace(drivers=lambda: [SqlServerSettings().driver], connect=Mock(return_value=connection))
                    with patch.dict("sys.modules", {"pyodbc": fake}), redirect_stdout(StringIO()), patch(
                        "mdd_forecasting.branch_database.pd.read_sql_query", return_value=pd.DataFrame()) as query:
                        query_branch_weather_sql(sql, SqlServerSettings(), start, end, "2025-08-01T12:00Z")
                    params = query.call_args.kwargs["params"]
                    shift = pd.Timedelta(hours=offset if legacy else 0)
                    self.assertEqual(params[:2], [(start.tz_localize(None) + shift).to_pydatetime(),
                                                 (end.tz_localize(None) + shift).to_pydatetime()])
                    self.assertEqual(params[2], datetime(2025, 8, 1, 12))
                    connection.close.assert_called_once()

    def test_bad_time_diagnostic_is_written_before_training(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "pogoda.csv"
            pd.DataFrame({"punkt": ["Łódź"], "dataGodzinaUTC": ["błędny UTC"],
                          "czasDanychZrodlaUTC": ["2024-12-31T12:00Z"]}).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "bledy_czasu_pogody.csv"):
                _weather(SimpleNamespace(weather=str(path)), PipelineConfig(), "2025-01-01", "2025-01-02",
                         "2025-01-01T00:00Z", diagnostics_dir=root)
            audit = pd.read_csv(root / "bledy_czasu_pogody.csv", sep=";")
            self.assertEqual(audit._raw_weather_valid_time_utc.tolist(), ["błędny UTC"])
            self.assertIn("BLEDNY_UTC_CELU", audit.kontrola_czasu_pogody.iloc[0])


if __name__ == "__main__":
    unittest.main()
