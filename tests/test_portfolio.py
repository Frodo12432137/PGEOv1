from __future__ import annotations

import unittest
import numpy as np
import pandas as pd

from mdd_forecasting.portfolio import analyze_portfolio, annotate_portfolio


def measurements(rows):
    data = pd.DataFrame(rows, columns=["oddzial_code", "kierunek_energii_norm", "klient_nazwa", "doba_handlowa", "wartosc_rzeczywista"])
    data["doba_handlowa"] = pd.to_datetime(data["doba_handlowa"])
    data["target_time_utc"] = data["doba_handlowa"].dt.tz_localize("Europe/Warsaw").dt.tz_convert("UTC")
    return data


class TestPortfolio(unittest.TestCase):
    def test_disappearing_client_is_suspect_but_zero_client_stays_observed(self):
        data = measurements([
            ["BIA", "POBRANIE", "DAWNY", "2025-01-01", 100],
            ["BIA", "POBRANIE", "ZERO", "2025-01-01", 0],
            ["BIA", "POBRANIE", "ZERO", "2025-01-20", 0],
        ])
        reports = analyze_portfolio(data, grace_days=14)
        clients = reports["portfel_mdd"].set_index("klient_nazwa")
        self.assertEqual(clients.loc["DAWNY", "status"], "DLUZSZY_BRAK_DO_WERYFIKACJI")
        self.assertEqual(clients.loc["ZERO", "status"], "POMIAR_NA_KONCU_EKSPORTU")
        self.assertTrue(clients["stan_umowy"].eq("NIEZNANY_BRAK_REJESTRU_UMOW").all())
        self.assertEqual(reports["podsumowanie_portfela"].iloc[0]["mdd_z_dluzszym_brakiem"], 1)
        # Analiza nie usuwa byłych ani podejrzanych klientów z historii.
        self.assertEqual(len(data), 3)

    def test_branches_have_independent_export_reference_dates(self):
        data = measurements([["BIA", "POBRANIE", "B", "2025-02-01", 20],
                             ["LUB", "POBRANIE", "L", "2025-01-01", 30]])
        clients = analyze_portfolio(data)["portfel_mdd"]
        self.assertTrue(clients["dni_bez_pomiarow_na_koncu"].eq(0).all())
        self.assertFalse(clients["dluzszy_brak"].any())

    def test_same_count_different_mdd_is_detected(self):
        data = measurements([["BIA", "POBRANIE", "A", "2025-01-01", 20],
                             ["BIA", "POBRANIE", "B", "2025-01-02", 30]])
        changes = analyze_portfolio(data)["zmiany_portfela_dzienne"]
        self.assertEqual(changes.liczba_mdd.tolist(), [1, 1])
        self.assertEqual(changes.iloc[1]["nowe_nazwy"], "B")
        self.assertEqual(changes.iloc[1]["brakujace_nazwy"], "A")
        self.assertEqual(changes.iloc[1]["status"], "ZMIANA_ZESTAWU_MDD")

    def test_measurements_returning_after_gap_do_not_prove_contract_end(self):
        data = measurements([["BIA", "POBRANIE", "A", "2025-01-01", 20],
                             ["BIA", "POBRANIE", "A", "2025-01-05", 30]])
        reports = analyze_portfolio(data)
        gap = reports["przerwy_pomiarow_mdd"].iloc[0]
        self.assertEqual(gap["dni_przerwy"], 3)
        self.assertEqual(gap["powrot_pomiarow"], pd.Timestamp("2025-01-05"))
        last_change = reports["zmiany_portfela_dzienne"].iloc[-1]
        self.assertEqual(last_change["status"], "PRZERWA_W_DANYCH_ODDZIALU")
        self.assertEqual(last_change["brakujace_w_danych"], 0)
        self.assertEqual(reports["portfel_mdd"].iloc[0]["status"], "POMIAR_NA_KONCU_EKSPORTU")

    def test_blank_value_is_not_a_measurement_but_zero_is(self):
        data = measurements([["BIA", "POBRANIE", "A", "2025-01-01", 20],
                             ["BIA", "POBRANIE", "A", "2025-01-20", np.nan],
                             ["BIA", "POBRANIE", "B", "2025-01-20", 0]])
        clients = analyze_portfolio(data)["portfel_mdd"].set_index("klient_nazwa")
        self.assertEqual(clients.loc["A", "dni_bez_pomiarow_na_koncu"], 19)
        self.assertEqual(clients.loc["B", "dni_bez_pomiarow_na_koncu"], 0)

    def test_prediction_marks_changed_membership_without_adjusting_energy(self):
        baseline_rows = [["BIA", d, "A", "2025-01-01", 10] for d in ["POBRANIE", "ODDANIE"]]
        current_rows = [["BIA", d, "B", "2025-01-02", 10] for d in ["POBRANIE", "ODDANIE"]]
        baseline = analyze_portfolio(measurements(baseline_rows))
        current = analyze_portfolio(measurements(current_rows))
        balance = pd.DataFrame({"oddzial_code": ["BIA"], "bilans": [40.0]})
        details = pd.DataFrame({"oddzial_code": ["BIA", "BIA"], "kierunek_energii_norm": ["POBRANIE", "ODDANIE"]})
        checked, checked_details = annotate_portfolio(balance, details, current, pd.Timestamp("2025-01-03T00:00Z"),
                                                       source="AKTUALNY_EKSPORT", baseline_reports=baseline)
        self.assertEqual(checked.iloc[0]["bilans"], 40)
        self.assertEqual(checked.iloc[0]["status_portfela"], "PORTFEL_DO_WERYFIKACJI")
        self.assertTrue(checked_details["status_portfela"].eq("ZMIANA_ZESTAWU_OD_TRENINGU_DO_WERYFIKACJI").all())

    def test_stale_snapshot_is_marked_without_claiming_clients_departed(self):
        rows = [["BIA", d, "A", "2025-01-01", 10] for d in ["POBRANIE", "ODDANIE"]]
        reports = analyze_portfolio(measurements(rows))
        balance = pd.DataFrame({"oddzial_code": ["BIA"]})
        details = pd.DataFrame({"oddzial_code": ["BIA", "BIA"], "kierunek_energii_norm": ["POBRANIE", "ODDANIE"]})
        _, checked = annotate_portfolio(balance, details, reports, pd.Timestamp("2025-02-01T00:00Z"))
        self.assertTrue(checked.status_portfela.eq("STARY_EKSPORT_PORTFELA").all())
        self.assertFalse(reports["portfel_mdd"]["dluzszy_brak"].any())


if __name__ == "__main__":
    unittest.main()
