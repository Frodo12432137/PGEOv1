"""Ustaw cztery daty, sprawdź ścieżki i kliknij ▶ Run Python File. Opis: BACKTEST.md."""
from uruchom_trening_bilansow import (
    SCIEZKA_ENERGII, SCIEZKA_POGODY, SCIEZKA_MAPOWANIA,
    PARAMETRY as PARAMETRY_TRENINGU, POGODA_SQL,
)
from mdd_forecasting.backtest_runtime import configured_backtest

# Ścieżki powyżej są przejęte z Twojego uruchom_trening_bilansow.py.
# Możesz je nadpisać tutaj, np. SCIEZKA_ENERGII = r"C:\Dane\energia.csv".
FOLDER_BACKTESTU = r"backtest_bilansu"  # Osobny folder; każdy przebieg ma własne raporty.

# Lokalne daty Europe/Warsaw, oba końce włącznie. Wpisz zakres dostępny w swoim pliku.
TRENING_OD = "2024-07-01"
TRENING_DO = "2025-06-27"
BACKTEST_OD = "2025-07-01"
BACKTEST_DO = "2025-07-31"
# BACKTEST_* to daty URUCHOMIEŃ prognozy. Każda przewiduje następne 72 h,
# więc realizacje energii i archiwum pogody powinny sięgać 3 dni dalej.
# Zostaw odstęp po treningu: label_delay_hours liczymy od końca ostatniego pomiaru.

# Ten sam model, grid, liczba iteracji, pogoda i limity próbek co w zwykłym treningu.
PARAMETRY = {**PARAMETRY_TRENINGU}
# Opcjonalne zmiany, np. PARAMETRY["threads"] = 4 lub PARAMETRY["max_iter"] = 300.
# Nie zwiększaj dokładności przez oglądanie i ponowne strojenie tego samego backtestu.

WYKONANIE = {
    "max_runtime_hours": 10,  # Limit całego procesu; po nim zatrzymanie i status NIEKOMPLETNY.
    "resume": True,  # Te same dane/ustawienia: użyj gotowych prób strojenia lub gotowego modelu.
    "report_every_origins": 10,  # Raport częściowy po pierwszej prognozie, potem co tyle przebiegów.
    "max_html_origins": 200,  # Limit wykresów HTML; pełne wyniki zawsze w CSV.
}

if __name__ == "__main__":
    raise SystemExit(configured_backtest(
        energy=SCIEZKA_ENERGII, weather_file=SCIEZKA_POGODY, mapping_file=SCIEZKA_MAPOWANIA,
        output_dir=FOLDER_BACKTESTU, train_from=TRENING_OD, train_to=TRENING_DO,
        backtest_from=BACKTEST_OD, backtest_to=BACKTEST_DO,
        parameters=PARAMETRY, weather_options=POGODA_SQL, runtime=WYKONANIE,
    ))
