import unittest

import pandas as pd

from mdd_forecasting.branch_model import _cap


class TestTrainingCap(unittest.TestCase):
    def test_preserves_older_branch_and_latest_rows_per_branch(self):
        frame = pd.DataFrame({
            "oddzial_code": ["RZE"] * 4 + ["BIA"] * 8,
            "target_time_utc": pd.date_range("2025-01-01", periods=12, freq="h", tz="UTC"),
            "forecast_origin_utc": pd.Timestamp("2024-12-31", tz="UTC"),
        }, index=range(100, 112))
        result = _cap(frame, 6)
        self.assertEqual(result.index.tolist(), [101, 102, 103, 109, 110, 111])
        self.assertEqual(result.groupby("oddzial_code").size().to_dict(), {"BIA": 3, "RZE": 3})
        pd.testing.assert_frame_equal(frame, _cap(frame, 20))

    def test_short_branch_releases_unused_quota_without_duplicates(self):
        frame = pd.DataFrame({
            "oddzial_code": ["RZE"] + ["BIA"] * 9,
            "target_time_utc": pd.date_range("2025-01-01", periods=10, freq="h", tz="UTC"),
            "forecast_origin_utc": pd.Timestamp("2024-12-31", tz="UTC"),
        })
        result = _cap(frame, 6)
        self.assertEqual(result.index.tolist(), [0, 5, 6, 7, 8, 9])
        with self.assertRaisesRegex(ValueError, "liczbie oddziałów"):
            _cap(frame, 1)
