"""Ustaw ścieżki i kliknij ▶ w VS Code. Wczytuje modele, nie uruchamia treningu."""
from mdd_forecasting.branch_launcher import configured_launch


FOLDER_MODELU = r"model_bilansu"  # Ten sam folder, do którego zapisał trening.
SCIEZKA_POGODY = r""  # Puste = domyślny SQL; własny .sql lub CSV/XLSX z aktualnymi wydaniami.
SCIEZKA_ENERGII = r""  # Opcjonalny Excel do audytu portfela. Puste = historia treningu.
FOLDER_PROGNOZY = r""  # Puste = osobny folder model_bilansu/prognozy/data_czas.
CHWILA_PROGNOZY = ""  # Puste = teraz; przykład: "2026-10-14T10:30:00+02:00".
HORYZONT_GODZIN = 72  # Można skrócić; zwiększenie poza zakres treningu wymaga retreningu.

POGODA_SQL = {"sql_owner": None, "sql_weather_type": None}


if __name__ == "__main__":
    raise SystemExit(configured_launch(
        training=False, energy=SCIEZKA_ENERGII, artifacts_dir=FOLDER_MODELU,
        weather_file=SCIEZKA_POGODY, output_dir=FOLDER_PROGNOZY,
        as_of=CHWILA_PROGNOZY, options={"horizon_hours": HORYZONT_GODZIN}, weather_options=POGODA_SQL,
    ))
