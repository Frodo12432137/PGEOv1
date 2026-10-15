"""Kontrola obecności MDD w pomiarach. Nie jest rejestrem umów."""
from __future__ import annotations

import numpy as np
import pandas as pd

SCOPE = ["oddzial_code", "kierunek_energii_norm"]
CLIENT = [*SCOPE, "klient_nazwa"]


def analyze_portfolio(measurements: pd.DataFrame, grace_days: int = 14, *, ignored_dates=()) -> dict[str, pd.DataFrame]:
    if grace_days < 1:
        raise ValueError("Okres kontroli portfela musi być dodatnią liczbą dni.")
    ignored = pd.DatetimeIndex(ignored_dates).dropna().normalize().unique().sort_values()

    def ignored_between(start, end):
        # Otwarte granice: oba końce są dniami z zachowanymi pomiarami.
        left = ignored.searchsorted(start, side="right")
        right = ignored.searchsorted(end, side="left")
        return np.maximum(right - left, 0)
    # Zero nadal jest pomiarem. Pusty I nie dowodzi obecności realizacji.
    data = measurements.loc[measurements["wartosc_rzeczywista"].notna()].copy()
    if data.empty:
        raise ValueError("Brak wartości pomiarów do kontroli portfela MDD.")
    day = data["doba_handlowa"]
    data["doba_handlowa"] = pd.to_datetime(day).dt.normalize()
    daily = data[[*CLIENT, "doba_handlowa"]].drop_duplicates()
    daily = daily.sort_values([*CLIENT, "doba_handlowa"])
    daily["poprzednia_doba"] = daily.groupby(CLIENT)["doba_handlowa"].shift()
    daily["dni_przerwy"] = (daily["doba_handlowa"] - daily["poprzednia_doba"]).dt.days - 1
    daily["dni_przerwy"] -= ignored_between(daily["poprzednia_doba"], daily["doba_handlowa"])
    gaps = daily.loc[daily["dni_przerwy"].gt(0), [*CLIENT, "poprzednia_doba", "doba_handlowa", "dni_przerwy"]].copy()
    gaps["brak_od"] = gaps["poprzednia_doba"] + pd.Timedelta(days=1)
    gaps["brak_do"] = gaps["doba_handlowa"] - pd.Timedelta(days=1)
    gaps = gaps.rename(columns={"doba_handlowa": "powrot_pomiarow"})
    gaps["status"] = "PRZERWA_I_POWROT_POMIAROW"
    clients = data.groupby(CLIENT).agg(
        pierwsza_doba=("doba_handlowa", "min"), ostatnia_doba=("doba_handlowa", "max"),
        ostatni_pomiar_utc=("target_time_utc", "max"), liczba_pomiarow=("target_time_utc", "nunique"),
        liczba_dob=("doba_handlowa", "nunique"), srednia_energia_na_godzine=("wartosc_rzeczywista", "mean"),
    ).reset_index()
    references = data.groupby(SCOPE).agg(
        koniec_eksportu_doba=("doba_handlowa", "max"), koniec_eksportu_utc=("target_time_utc", "max"),
    ).reset_index()
    clients = clients.merge(references, on=SCOPE, validate="many_to_one")
    clients["dni_bez_pomiarow_na_koncu"] = (clients["koniec_eksportu_doba"] - clients["ostatnia_doba"]).dt.days
    clients["dni_bez_pomiarow_na_koncu"] -= ignored_between(clients["ostatnia_doba"], clients["koniec_eksportu_doba"])
    clients["dluzszy_brak"] = clients["dni_bez_pomiarow_na_koncu"].ge(grace_days)
    clients["status"] = np.select(
        [clients["dluzszy_brak"], clients["dni_bez_pomiarow_na_koncu"].gt(0)],
        ["DLUZSZY_BRAK_DO_WERYFIKACJI", "BRAK_NA_KONCU_EKSPORTU"],
        default="POMIAR_NA_KONCU_EKSPORTU",
    )
    gap_counts = gaps.groupby(CLIENT).size().rename("liczba_przerw").reset_index()
    clients = clients.merge(gap_counts, on=CLIENT, how="left", validate="one_to_one")
    clients["liczba_przerw"] = clients["liczba_przerw"].fillna(0).astype(int)
    clients["stan_umowy"] = "NIEZNANY_BRAK_REJESTRU_UMOW"
    changes = []
    for scope, group in daily.groupby(SCOPE, sort=True):
        previous_day, previous_members = None, set()
        for date, rows in group.groupby("doba_handlowa", sort=True):
            members = set(rows["klient_nazwa"].astype(str))
            added, missing = set(), set()
            if previous_day is None:
                status = "PIERWSZA_DOBA_EKSPORTU"
            elif (date - previous_day).days - int(ignored_between(previous_day, date)) > 1:
                status = "PRZERWA_W_DANYCH_ODDZIALU"
            else:
                added, missing = members - previous_members, previous_members - members
                status = "ZMIANA_ZESTAWU_MDD" if added or missing else "BEZ_ZMIANY"
            changes.append({**dict(zip(SCOPE, scope)), "doba_handlowa": date, "liczba_mdd": len(members),
                            "nowe_w_danych": len(added), "brakujace_w_danych": len(missing),
                            "nowe_nazwy": "; ".join(sorted(added)), "brakujace_nazwy": "; ".join(sorted(missing)),
                            "status": status})
            previous_day, previous_members = date, members
    summary_rows = []
    for scope, group in clients.groupby(SCOPE, sort=True):
        last = group["koniec_eksportu_doba"].iloc[0]
        latest = sorted(group.loc[group["ostatnia_doba"].eq(last), "klient_nazwa"].astype(str))
        suspect = sorted(group.loc[group["dluzszy_brak"], "klient_nazwa"].astype(str))
        summary_rows.append({**dict(zip(SCOPE, scope)), "koniec_eksportu_doba": last,
                             "koniec_eksportu_utc": group["koniec_eksportu_utc"].iloc[0],
                             "liczba_mdd_historycznych": len(group), "liczba_mdd_na_koncu": len(latest),
                             "mdd_z_dluzszym_brakiem": len(suspect),
                             "przyklady_do_weryfikacji": "; ".join(suspect[:10]),
                             "mdd_na_koncu": latest,
                             "status_portfela": "DO_WERYFIKACJI" if suspect else "BEZ_DLUGICH_BRAKOW_W_POMIARACH"})
    return {"portfel_mdd": clients, "przerwy_pomiarow_mdd": gaps,
            "zmiany_portfela_dzienne": pd.DataFrame(changes),
            "podsumowanie_portfela": pd.DataFrame(summary_rows)}


def annotate_portfolio(balance: pd.DataFrame, details: pd.DataFrame, reports: dict | None,
                       origin: pd.Timestamp, grace_days: int = 14, source: str = "HISTORIA_TRENINGU",
                       baseline_reports: dict | None = None):
    balance, details = balance.copy(), details.copy()
    if not reports:
        balance["status_portfela"] = "BRAK_KONTROLI_PORTFELA_W_MODELU"
        details["status_portfela"] = "BRAK_KONTROLI_PORTFELA_W_MODELU"
        return balance, details
    summary = reports["podsumowanie_portfela"].copy()
    summary["zmiana_zestawu_od_treningu"] = False
    if baseline_reports:
        baseline = baseline_reports["podsumowanie_portfela"].set_index(SCOPE)
        for index, row in summary.iterrows():
            key = tuple(row[k] for k in SCOPE)
            previous = set(baseline.loc[key, "mdd_na_koncu"]) if key in baseline.index else set()
            summary.loc[index, "zmiana_zestawu_od_treningu"] = previous != set(row["mdd_na_koncu"])
    age = (origin - pd.to_datetime(summary["koniec_eksportu_utc"], utc=True)).dt.total_seconds() / 86400
    summary.loc[age.ge(grace_days), "status_portfela"] = "STARY_EKSPORT_PORTFELA"
    summary.loc[summary["zmiana_zestawu_od_treningu"], "status_portfela"] = "ZMIANA_ZESTAWU_OD_TRENINGU_DO_WERYFIKACJI"
    summary["zrodlo_kontroli_portfela"] = source
    summary["wiek_eksportu_dni"] = age
    selected = [*SCOPE, "status_portfela", "liczba_mdd_historycznych", "liczba_mdd_na_koncu",
                "mdd_z_dluzszym_brakiem", "koniec_eksportu_utc", "zrodlo_kontroli_portfela", "wiek_eksportu_dni"]
    details = details.merge(summary[selected], on=SCOPE, how="left", validate="many_to_one")
    details["status_portfela"] = details["status_portfela"].fillna("BRAK_DANYCH_PORTFELA_ODDZIALU")
    clean = "BEZ_DLUGICH_BRAKOW_W_POMIARACH"
    for direction, prefix in [("POBRANIE", "pobranie"), ("ODDANIE", "oddanie")]:
        part = summary.loc[summary["kierunek_energii_norm"].eq(direction), ["oddzial_code", "status_portfela"]]
        part = part.rename(columns={"status_portfela": f"{prefix}_status_portfela"})
        balance = balance.merge(part, on="oddzial_code", how="left", validate="many_to_one")
        balance[f"{prefix}_status_portfela"] = balance[f"{prefix}_status_portfela"].fillna("BRAK_DANYCH_PORTFELA_ODDZIALU")
    okay = balance["pobranie_status_portfela"].eq(clean) & balance["oddanie_status_portfela"].eq(clean)
    balance["status_portfela"] = np.where(okay, clean, "PORTFEL_DO_WERYFIKACJI")
    return balance, details
