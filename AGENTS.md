# Instrukcje dla AI pracujących nad PGEOv1

## Zakres i cel

Projekt prognozuje godzinowe pobranie, oddanie i **bilans = pobranie − oddanie**
osobno dla ośmiu oddziałów/OSD. Horyzont domyślny to następne 72 pełne godziny.
Trening zapisuje modele; predykcję można uruchamiać kilka razy dziennie bez uczenia.
Odpowiadaj użytkownikowi po polsku i opisuj wynik oraz ograniczenia prostym językiem.

Te instrukcje dotyczą `mdd_forecasting/`, trzech programów `uruchom_*` tego
modelu, jego testów i dokumentacji. Jeśli checkout jest częścią większego
workspace PGE, nie rozszerzaj zadania na inne projekty bez potrzeby.

## Od czego zacząć

1. Przeczytaj [kontekst i ustalenia](docs/AI_CONTEXT.md).
2. Otwórz [mapę architektury](docs/ARCHITECTURE.md), aby znaleźć właściwe moduły.
3. Zasady wejścia, polecenia i raporty opisuje [instrukcja 72 h](START_BILANSY_72H.md).
4. Wybierz właściwy skill z tabeli poniżej. Nie trzeba wczytywać wszystkich.

| Zadanie | Skill |
|---|---|
| Excel/CSV, korekty, czas, pogoda, obecność MDD | [pgeo-data-audit](.agents/skills/pgeo-data-audit/SKILL.md) |
| Trening, wybór pogody, parametry, ocena dokładności | [pgeo-train-evaluate](.agents/skills/pgeo-train-evaluate/SKILL.md) |
| Wczytanie modeli i prognoza kolejnych 72 godzin | [pgeo-predict-72h](.agents/skills/pgeo-predict-72h/SKILL.md) |

Skille są projektowe i przenośne. AI bez obsługi automatycznej selekcji skilli
może odczytać wskazany `SKILL.md` jak zwykłą instrukcję.

## Zasady, które trzeba zachować

- Agreguj MDD w ramach oddziału i kierunku. Modele są wspólne dla oddziałów,
  ale prognozy i bilanse pozostają osobne. Osiem oddziałów × 72 godziny to 576 wierszy.
- Najpierw wybierz korekty pomiarów: **4 > 3 > 2 > 1 > 0**. Nie sumuj wersji.
  Sprzeczne najlepsze wersje wymagają wyjaśnienia; nie wybieraj jednej losowo.
- Dla całego przebiegu predykcji obowiązuje jeden `origin`/`as-of`. Do każdej
  godziny docelowej wybierz najnowszą pogodę dostępną **nie później niż origin**.
  `ABS(DATEDIFF(...))`, globalne flagi ostatniej prognozy i późniejsze wydania
  nie odtwarzają informacji dostępnej w chwili uruchomienia.
- Klucze czasu łącz po UTC. Kalendarz i eksport handlowy używają `Europe/Warsaw`.
  Zachowaj obsługę dób 23/25 godzin; nie utożsamiaj godziny handlowej 25 z północą.
- Nie zamieniaj brakującego pomiaru na zero. Zero jest pomiarem.
  Brak prognozy kierunku oznacza brak bilansu i jawny status.
- Historia pomiarów nie potwierdza aktywności umowy. Nie usuwaj automatycznie
  klientów ani nie skaluj prognozy liczbą MDD po samym zaniku danych.
- Dobieraj cechy i parametry na chronologicznej walidacji; odłożony test służy
  ocenie. Lagi energii są wyłączone, bo nie znamy czasów udostępnienia korekt.
- Predykcja wczytuje artefakty i nie wywołuje `fit()`. Zmiana schematu cech lub
  formatu modelu wymaga zgodnego zapisu/odczytu i opisania potrzeby retreningu.
- Zachowaj osobny plik SQL i wykonanie przez Python/pyodbc z parametrami.
  Komputer służbowy używa ODBC 17 oraz `Trusted_Connection` konta Windows.

## Praca i weryfikacja

Nowy przepływ: `python -m mdd_forecasting.train` oraz
`python -m mdd_forecasting.predict`. `python -m mdd_forecasting` i
`uruchom_model.py` to starszy przepływ MDD, który ponownie trenuje modele.

```bash
python -m pip install -r mdd_forecasting/requirements.txt
python -m unittest tests.test_portfolio tests.test_branch_forecasting tests.test_mdd_forecasting -v
python -m unittest tests.test_branch_training_tools -v
```

Uruchamiaj sprawdzenia odpowiednie do zmiany. Same opisy i skille wymagają
sprawdzenia linków, poleceń i zgodności z kodem, a nie kosztownego treningu.
Nie przedstawiaj syntetycznych testów jako oceny jakości na danych PGE.
Po zmianie konfiguracji treningu i raportów uwzględnij `tests.test_branch_training_tools`;
jego próba CatBoost wymaga opcjonalnej biblioteki. Ustawienia użytkownika i opis
raportu Excel są w [PARAMETRY_I_RAPORTY.md](PARAMETRY_I_RAPORTY.md).
Zgłaszaj różnicę między wynikiem sprawdzonym, założeniem i rzeczą nieprzetestowaną.

## Dane i publikowanie

Repozytorium ma pozostać prywatne. Publikuj tylko pliki związane z zadaniem.
Rzeczywiste pomiary, dane klientów, lokalne connection stringi, modele i raporty
nie są częścią kodu źródłowego; nie dodawaj ich bez wyraźnej prośby użytkownika.
Skille nie udzielają nowych uprawnień do baz ani zewnętrznych usług.
