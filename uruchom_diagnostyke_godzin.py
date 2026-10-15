"""Otwórz ten plik i kliknij ▶ Run Python File w VS Code.

Program sprawdza cały eksport energii, bez SQL i treningu. Nie zmienia danych.
Puste ustawienia pobierają ścieżki z uruchom_trening_bilansow.py.
"""
from mdd_forecasting.energy_time_diagnostics import diagnostic_launch


SCIEZKA_ENERGII = r""  # Puste = ścieżka z treningu; jeśli tam też pusto, okno wyboru.
FOLDER_RAPORTU = r""  # Puste = FOLDER_MODELU/diagnostyka_godzin.


if __name__ == "__main__":
    raise SystemExit(diagnostic_launch(SCIEZKA_ENERGII, FOLDER_RAPORTU))
