from __future__ import annotations

from pathlib import Path
import os
import pandas as pd

from .branch_data import utc_timestamp
from .database import SqlServerSettings, _sql_datetime

DEFAULT_BRANCH_SQL = Path(__file__).resolve().parent / "sql" / "pogoda_bilans_historia.sql"


def query_branch_weather_sql(sql_path: str | Path, settings: SqlServerSettings,
                             valid_from: object, valid_to: object, asof: object,
                             owner: str | None = None, weather_type: str | None = None,
                             query_timeout: int = 0) -> pd.DataFrame:
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError("Brak pyodbc. Zainstaluj requirements.txt i ODBC Driver 17 for SQL Server.") from exc
    if not os.getenv("MDD_SQL_CONNECTION_STRING") and settings.driver not in pyodbc.drivers():
        raise RuntimeError(f"Brak sterownika ODBC {settings.driver!r}.")
    query = Path(sql_path).read_text(encoding="utf-8-sig")
    # Pięć parametrów DECLARE; komentarze nie liczą się jako placeholdery.
    start, end = _sql_datetime(valid_from, "od"), _sql_datetime(valid_to, "do")
    if start >= end:
        raise ValueError("Początek zakresu SQL musi być wcześniejszy niż koniec.")
    params = [start, end, utc_timestamp(asof).tz_localize(None).to_pydatetime(),
              owner or None, weather_type or None]
    connection = pyodbc.connect(settings.connection_string(), timeout=settings.connect_timeout_seconds, autocommit=True)
    try:
        if query_timeout > 0:
            connection.timeout = query_timeout
        result = pd.read_sql_query(query, connection, params=params)
    finally:
        connection.close()
    result.columns = [str(c).strip() for c in result.columns]
    return result
