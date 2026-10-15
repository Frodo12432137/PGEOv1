"""Kompletność i świeżość pogody oraz jej wybór na walidacji, bez pozornej oceny meteo."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import WEATHER_FEATURES


def weather_quality_tables(joined: pd.DataFrame, timezone: str) -> dict[str, pd.DataFrame]:
    # Pobranie i oddanie używają tego samego wydania. Jedna ocena na origin/cel/OSD.
    frame = joined.drop_duplicates(["oddzial_code", "forecast_origin_utc", "target_time_utc"]).copy()
    frame["rok_miesiac"] = frame.target_time_utc.dt.tz_convert(timezone).dt.strftime("%Y-%m")
    frame["zakres_h"] = ((frame.horyzont_h - 1) // 24).map(lambda value: f"{int(value)*24+1}-{int(value+1)*24}")
    nonnegative = set(WEATHER_FEATURES) - {"temperatura", "kierunek_wiatru"}
    quality = []
    for (branch, month), group in frame.groupby(["oddzial_code", "rok_miesiac"]):
        for variable in WEATHER_FEATURES:
            values = pd.to_numeric(group[variable], errors="coerce")
            finite = values.notna() & np.isfinite(values)
            sample = values.loc[finite]
            suspicious = pd.Series(False, index=values.index)
            if variable in nonnegative:
                suspicious = values.lt(0) & finite
            elif variable == "kierunek_wiatru":
                suspicious = (values.lt(0) | values.gt(360)) & finite
            elif variable == "temperatura":
                suspicious = (values.lt(-100) | values.gt(70)) & finite
            status = "BRAK_CECHY" if sample.empty else ("DO_WERYFIKACJI" if suspicious.any() or not finite.all() else "DANE_KOMPLETNE")
            quality.append({"oddzial_code": branch, "rok_miesiac": month, "zmienna": variable,
                            "n_oczekiwane": len(group), "n_poprawne_liczby": int(finite.sum()),
                            "braki": int(values.isna().sum()), "nieskonczone": int(np.isinf(values).sum()),
                            "pokrycie": float(finite.mean()), "podejrzany_zakres": int(suspicious.sum()),
                            "minimum": sample.min(), "p01": sample.quantile(.01), "mediana": sample.median(),
                            "p99": sample.quantile(.99), "maksimum": sample.max(), "status": status,
                            "interpretacja": "Kontrola wejścia, nie trafność prognozy meteo. Zakresy orientacyjne; potwierdź jednostki. Niczego nie usunięto."})

    availability = []
    for (branch, month, horizon), group in frame.groupby(["oddzial_code", "rok_miesiac", "zakres_h"]):
        matched = group.pogoda_dopasowana
        age = pd.to_numeric(group.loc[matched, "wiek_prognozy_h"], errors="coerce")
        lead = age + group.loc[matched, "horyzont_h"]
        availability.append({"oddzial_code": branch, "rok_miesiac": month, "horyzont": horizon,
                             "n_oczekiwane": len(group), "n_z_wydaniem_asof": int(matched.sum()),
                             "pokrycie_wydaniami": float(matched.mean()), "brak_wydania_asof": int((~matched).sum()),
                             "wiek_wydania_mediana_h": age.median(), "wiek_wydania_p95_h": age.quantile(.95),
                             "wiek_wydania_max_h": age.max(), "liczba_wydan_starszych_24h": int(age.gt(24).sum()),
                             "wyprzedzenie_meteo_mediana_h": lead.median(),
                             "trafnosc_meteo": "NIEOCENIONA_BRAK_OBSERWACJI_METEO",
                             "uwaga": "Brak wydania oznacza brak prognozy pogody dostępnej przed origin; nie zastępuj późniejszym wydaniem."})
    return {"jakosc_pogody_zmienne": pd.DataFrame(quality), "jakosc_pogody_wydania": pd.DataFrame(availability)}


def weather_variant_recommendations(comparisons: pd.DataFrame) -> pd.DataFrame:
    singles = comparisons.loc[comparisons.backend.ne("ensemble")]
    rows = []
    for direction, group in singles.groupby("kierunek"):
        calendar = group.loc[group.wariant.eq("kalendarz"), "walidacja_macro_mae_norm"]
        reference = calendar.min() if len(calendar) else np.nan
        for variant, variants in group.groupby("wariant"):
            best = variants.loc[variants.walidacja_macro_mae_norm.idxmin()]
            score = float(best.walidacja_macro_mae_norm)
            rows.append({"kierunek": direction, "wariant": variant, "najlepszy_backend": best.backend,
                         "walidacja_macro_mae_norm": score, "kalendarz_macro_mae_norm": reference,
                         "poprawa_vs_kalendarz": 1 - score / reference if reference > 0 else np.nan,
                         "liczba_cech": int(best.liczba_cech), "n_walidacji": int(best.liczba_wierszy),
                         "interpretacja": "Najlepsza testowana konfiguracja danej pogody na wcześniejszej walidacji; nie jest to pomiar trafności meteo ani dowód przyczynowości."})
    result = pd.DataFrame(rows)
    if not result.empty:
        result["ranking_walidacji"] = result.groupby("kierunek").walidacja_macro_mae_norm.rank(method="min").astype(int)
    return result
