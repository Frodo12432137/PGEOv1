# PGEOv1 — prognozy bilansów energii na 72 godziny

System przygotowuje osobny godzinowy bilans dla każdego oddziału/OSD PGE:
**pobranie minus oddanie**. Osiem oddziałów daje 576 wierszy prognozy 72 h.

## Uruchomienie w VS Code

1. Pobierz lub sklonuj repozytorium i otwórz cały folder w VS Code.
2. Zainstaluj biblioteki: `python -m pip install -r requirements.txt`.
3. Otwórz **`uruchom_trening_bilansow.py`**, wpisz `SCIEZKA_ENERGII`,
   `FOLDER_MODELU` i ustawienia w `PARAMETRY`, zapisz plik i kliknij ▶.
   Pusta ścieżka energii otwiera wybór pliku. Trening porównuje modele i pogodę.
4. Uruchamiaj **`uruchom_prognoze_bilansow.py`** kilka razy dziennie.
   Program wczytuje zapisany model i nie wykonuje ponownego uczenia.

Trening domyślnie pomija całe doby zmiany czasu (23 i 25 h) w `Europe/Warsaw`,
przed walidacją i agregacją energii. Daty i liczby rekordów zapisuje w
`pominiete_dni_zmiany_czasu.csv` oraz arkuszu `Pominiete_doby` raportu Excel.
W pliku treningowym odpowiada za to `include_dst_days=False`.

Połączenie z firmowym SQL wymaga ODBC Driver 17 for SQL Server, sieci/VPN
i uprawnień konta Windows. Kwerenda jest osobnym plikiem wykonywanym przez
Python/pyodbc. Dostępne jest też użycie eksportu pogody zamiast połączenia z SQL.

**[Pełna instrukcja, format wejścia, parametry i ograniczenia](START_BILANSY_72H.md).**

**[Ustawienia w kodzie, wybór modeli i czytanie statystyk](PARAMETRY_I_RAPORTY.md).**
Po treningu otwórz `raport_treningu.xlsx`: błędy według oddziałów, godzin, dni,
horyzontu i warunków pogody, największe pomyłki, porównanie z profilem oraz wykresy.
`models="auto"` porównuje HistGradientBoosting i opcjonalnie CatBoost; jego instalacja:
`python -m pip install -r mdd_forecasting/requirements-production.txt`.

Backtest: otwórz **`uruchom_backtest_bilansow.py`**, ustaw daty treningu i testu,
kliknij ▶. Ten sam model, osobny późniejszy okres oceny, limit czasu i wznowienie.
Wyniki: interaktywny `raport_backtestu.html`, Excel z wykresami i pełne CSV.
**[Instrukcja krok po kroku](BACKTEST.md).** Całe doby zmiany czasu pozostają
domyślnie wyłączone; dawny osobny audyt godzin został usunięty.

## Dla AI rozwijających projekt

Zacznij od **[AGENTS.md](AGENTS.md)**. [Kontekst projektu](docs/AI_CONTEXT.md)
wyjaśnia ustalenia biznesowe, dostępne dane i założenia do potwierdzenia,
a [mapa architektury](docs/ARCHITECTURE.md) wskazuje moduły i ich odpowiedzialność.

Projekt zawiera trzy skille w `.agents/skills/`:

- [Kontrola danych i portfela](.agents/skills/pgeo-data-audit/SKILL.md).
- [Trening i ocena modeli](.agents/skills/pgeo-train-evaluate/SKILL.md).
- [Prognoza 72 h bez ponownego uczenia](.agents/skills/pgeo-predict-72h/SKILL.md).

Każde AI może odczytać pliki `SKILL.md` bezpośrednio.
Dodano też wejścia dla Claude (`CLAUDE.md`) i GitHub Copilot
(`.github/copilot-instructions.md`), prowadzące do wspólnych instrukcji.

## Dane i pogoda

- Excel/CSV: dziewięć kolumn A:I opisanych w instrukcji.
- Korekty pomiarów: jeden rekord MDD/godzina/kierunek, najwyższa wersja 4 > 3 > 2 > 1 > 0.
- Pogoda: najnowsze wydanie dostępne w chwili uruchomienia całej prognozy 72 h.
- Kalendarz, pogoda oraz mapowanie oddziałów są przygotowane identycznie w treningu i predykcji.
- Dobór zmiennych ma osobne okno walidacji. Późniejszy test ocenia wybrany model.
- Dwa modele uczą się pobrania i oddania. Wyniki poszczególnych oddziałów są oddzielne.

## Kontrola zmian klientów

Raporty wykazują obecność MDD, dłuższe braki, przerwy, powroty i zmiany zestawu
klientów. Nowy eksport energii może być użyty do kontroli przy predykcji bez
ponownego treningu. Brak pomiarów nie potwierdza zakończenia umowy, a wartość
zero nadal jest pomiarem. Domyślny próg kontroli to 14 dni i jest ustawialny.

Kolumna `status_portfela` wskazuje niepewne lub zmienione portfele.
Kontrola nie przelicza automatycznie prognozy po domniemanym odejściu klientów.

## Testy

```bash
python -m unittest discover -v
```

Testy sprawdzają m.in. wybór korekt, znak bilansu, cutoff pogody, zmianę czasu,
zapis i odczyt modeli, prognozę 576 wierszy oraz kontrolę znikających MDD.
Sprawdzają też konfigurację strzałki, raport Excel, pokrycie oceny każdego oddziału,
luki godzinowe i opcjonalny CatBoost (ten test wymaga jego instalacji).
Testy korzystają z danych syntetycznych. Do właściwego treningu i oceny dokładności
na portfelu PGE potrzebny jest rzeczywisty eksport energii oraz historia pogody.

## Starszy przebieg MDD

`uruchom_model.py` oraz `python -m mdd_forecasting` obsługują wcześniejszy
przebieg prognoz poszczególnych MDD, który przy każdym uruchomieniu trenuje modele.
Do nowej prognozy bilansów 72 h używaj dwóch programów wskazanych powyżej.
