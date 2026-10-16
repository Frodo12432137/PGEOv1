"""Wspólna dla treningu i prognozy kontrola jawnych czasów UTC pogody."""
from __future__ import annotations

import warnings

import pandas as pd


class WeatherTimeError(ValueError):
    def __init__(self, diagnostics: pd.DataFrame):
        self.diagnostics = diagnostics
        examples = diagnostics.head(3).to_dict("records")
        super().__init__(
            f"Nie można jednoznacznie ustalić czasu UTC pogody: {len(diagnostics)} rekordów. "
            "Nie zastępujemy błędnego UTC czasem CET ani nie zgadujemy godzin zmiany czasu. "
            f"Przykłady: {examples}"
        )


def _present(values: pd.Series) -> pd.Series:
    return values.notna() & values.astype("string").str.strip().ne("").fillna(False)


def _local_to_utc(values: pd.Series, timezone: str) -> pd.Series:
    # Naive wartości to czas lokalny; jawny offset/Z już określa chwilę.
    # Tymczasowe utc=True pozwala wektorowo odczytać mieszane formaty, bez
    # uznawania naiwnego CET za UTC w wyniku końcowym.
    parsed = pd.to_datetime(values, errors="coerce", format="mixed", utc=True)
    aware = values.astype("string").str.contains(r"(?:Z|[+-]\d{2}:?\d{2})\s*$", case=False, na=False)
    naive = ~aware
    parsed.loc[naive] = parsed.loc[naive].dt.tz_localize(None).dt.tz_localize(
        timezone, ambiguous="NaT", nonexistent="NaT"
    ).dt.tz_convert("UTC")
    return parsed.astype("datetime64[ns, UTC]")


def time_diagnostics(weather: pd.DataFrame) -> pd.DataFrame:
    columns = ["_weather_source_row", "punkt", "weather_valid_time_utc", "weather_issue_time_utc",
               "zrodlo_czasu_celu", "zrodlo_czasu_wydania", "kontrola_czasu_pogody"]
    columns += [f"_raw_{column}" for column in ["weather_valid_time", "weather_valid_time_utc",
                "weather_issue_time", "weather_issue_time_utc", "weather_trade_date", "weather_trade_hour"]]
    return weather[columns].rename(columns={"_weather_source_row": "rekord_zrodla"}).sort_values("rekord_zrodla")


def normalize_weather_time(weather: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """UTC ma pierwszeństwo. Brak UTC dopuszcza wyłącznie pewną konwersję CET."""
    df = weather.copy()
    df["kontrola_czasu_pogody"] = ""
    fatal = pd.Series(False, index=df.index)

    def flag(mask, label, *, error=False):
        nonlocal fatal
        mask = mask.fillna(False)
        df.loc[mask, "kontrola_czasu_pogody"] += label + ";"
        if error:
            fatal |= mask

    flag(df["punkt"].isna() | df["_punkt_key"].eq(""), "BRAK_PUNKTU", error=True)
    for stem, label in [("valid", "celu"), ("issue", "wydania")]:
        utc_col, local_col = f"weather_{stem}_time_utc", f"weather_{stem}_time"
        utc_supplied = _present(df[f"_raw_{utc_col}"])
        local_supplied = _present(df[f"_raw_{local_col}"])
        local_utc = _local_to_utc(df[f"_raw_{local_col}"], timezone)
        df[utc_col] = df[utc_col].astype("datetime64[ns, UTC]")
        flag(utc_supplied & df[utc_col].isna(), f"BLEDNY_UTC_{label.upper()}", error=True)
        fallback = ~utc_supplied
        df.loc[fallback, utc_col] = local_utc.loc[fallback]
        df[f"zrodlo_czasu_{label}"] = "UTC_ZRODLOWE"
        df.loc[fallback, f"zrodlo_czasu_{label}"] = "CET_PRZELICZONY_NA_UTC"
        flag(fallback & df[utc_col].isna(), f"BRAK_JEDNOZNACZNEGO_CZASU_{label.upper()}", error=True)
        expected_local = df[utc_col].dt.tz_convert(timezone).dt.tz_localize(None)
        # Dwie jesienne 02:00 mają taki sam poprawny opis CET. Przy jawnym
        # UTC brak offsetu w tym opisie nie oznacza rozbieżności.
        local_matches = local_utc.eq(df[utc_col]) | (local_utc.isna() & df[local_col].eq(expected_local))
        flag(utc_supplied & df[utc_col].notna() & local_supplied
             & ~local_matches, f"POMINIETA_ROZBIEZNOSC_CET_{label.upper()}")
        # Dalsza część projektu dostaje spójny lokalny opis tej samej chwili.
        df[local_col] = expected_local

    # Godziny handlowe są wyłącznie metadanymi. Nie dopasowujemy UTC do
    # niepotwierdzonej konwencji H25 i nie przenumerowujemy rekordów.
    supplied_date = _present(df["_raw_weather_trade_date"])
    supplied_hour = _present(df["_raw_weather_trade_hour"])
    has_trade = supplied_date | supplied_hour
    # Import lokalny unika zależności cyklicznej z branch_data.
    from .branch_data import trade_time_utc
    expected = trade_time_utc(df["weather_trade_date"], df["weather_trade_hour"], timezone)
    flag(has_trade & (expected.isna() | expected.ne(df["weather_valid_time_utc"])),
         "POMINIETA_ROZBIEZNOSC_KLUCZA_HANDLOWEGO")
    valid = df["weather_valid_time_utc"]
    flag(valid.notna() & valid.ne(valid.dt.floor("h")), "CEL_POZA_PELNA_GODZINA", error=True)
    # Późne wydania pozostają w audycie; dodatni horyzont i as-of uniemożliwiają
    # ich wybór do prognozy celu leżącego przed wydaniem.
    flag(df["weather_issue_time_utc"].gt(valid), "WYDANIE_PO_CZASIE_CELU")
    df["kontrola_czasu_pogody"] = df["kontrola_czasu_pogody"].str.rstrip(";").replace("", "OK")
    if fatal.any():
        raise WeatherTimeError(time_diagnostics(df.loc[fatal]))
    differences = df["kontrola_czasu_pogody"].ne("OK")
    if differences.any():
        warnings.warn(
            f"Pogoda: {int(differences.sum())} rekordów wymaga uwagi w kontroli czasu. "
            "Używam czasów UTC; szczegóły: rozbieznosci_czasu_pogody.csv.", UserWarning, stacklevel=2)
    df["weather_lead_hours"] = (valid - df["weather_issue_time_utc"]).dt.total_seconds() / 3600
    return df.sort_values("_weather_source_row")


def weather_time_reports(weather: pd.DataFrame) -> dict[str, pd.DataFrame]:
    columns = ["zrodlo_czasu_celu", "zrodlo_czasu_wydania", "kontrola_czasu_pogody"]
    summary = weather.groupby(columns, dropna=False).size().rename("rekordy").reset_index()
    differences = time_diagnostics(weather.loc[weather.kontrola_czasu_pogody.ne("OK")])
    return {"kontrola_czasu_pogody": summary, "rozbieznosci_czasu_pogody": differences}
