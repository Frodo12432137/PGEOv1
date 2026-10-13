---
name: pgeo-predict-72h
description: Przygotuj lub diagnozuj prognozę kolejnych 72 godzin bilansu każdego oddziału PGEOv1 z zapisanych modeli, bez ponownego treningu. Używaj przy uruchamianiu predykcji, brakach pogody i interpretacji wyników.
---

# Prognoza 72 h PGEOv1

Przeczytaj [kontekst](../../../docs/AI_CONTEXT.md) i sekcję predykcji w
[instrukcji](../../../START_BILANSY_72H.md). Pracuj z katalogu głównego repozytorium.

## Przygotuj wejścia

Potrzebne są folder z `modele_oddzialow.joblib` i `manifest_oddzialy.json`
oraz aktualna pogoda z czasem dostępności każdego wydania.
Używaj artefaktów z zaufanego treningu. Wersja scikit-learn ma odpowiadać
manifestowi; przy braku artefaktów nie twórz fikcyjnego modelu produkcyjnego.
Opis uruchomienia SQL na firmowym Windows jest w instrukcji.

Ustal jeden `as-of` całego przebiegu. Domyślnie jest to bieżąca chwila.
Musi być po końcu historii treningowej. Jawny czas podawaj z offsetem
strefy, szczególnie przy zmianie czasu. Prognoza startuje od następnej
pełnej godziny i obejmuje 72 rzeczywiste godziny liczone w UTC.

## Uruchom bez uczenia

```bash
python -m mdd_forecasting.predict --artifacts-dir model_bilansu --weather pogoda_aktualna.csv --output-dir prognozy/przebieg_001
```

SQL: zastąp `--weather pogoda_aktualna.csv` przez
`--weather-sql mdd_forecasting/sql/pogoda_bilans_historia.sql`.
GUI: `python uruchom_prognoze_bilansow.py` lub strzałka VS Code.
Opcje sprawdź przez `python -m mdd_forecasting.predict --help`.
Do kolejnego przebiegu użyj osobnego folderu wynikowego, aby zachować poprzedni.

Opcjonalny `--energy aktualna_energia.csv` kontroluje zmiany portfela MDD,
bez treningu i bez modyfikacji wolumenów. Plik nie może zawierać pomiarów
późniejszych niż as-of. Bez niego audyt opiera się na historii treningowej.
`--horizon-hours` może skrócić horyzont; rozszerzenie poza treningowy wymaga treningu.

Do wszystkich godzin wybierz najnowszą pogodę dostępną do tego samego as-of.
Nie zmieniaj cutoffu na chwilę docelową. Korzystaj z mapowania zapisanego z modelem.
Nie uruchamiaj starszego `uruchom_model.py` lub `python -m mdd_forecasting`
jako zamiennika: tamten przepływ uczy się ponownie.

## Sprawdź wynik

`prognoza_bilansu_72h.csv` ma pobranie, oddanie i ich różnicę dla każdego
oddziału. Osiem oddziałów × 72 godziny daje 576 wierszy. Sprawdź unikalność
oddział/godzina UTC, zakres czasu i oba kierunki. Bilans może być ujemny.
CSV używa separatora `;`, przecinka dziesiętnego i UTF-8 BOM.

Rozróżniaj `status` prognozy od `status_portfela`. Brak wymaganej pogody
pozostawia pustą prognozę i status, nie zero; CLI zwraca wtedy kod 2 i zapisuje
CSV do diagnozy. Wariant kalendarzowy może działać bez pogody.
Portfel wymagający sprawdzenia może towarzyszyć liczbowej prognozie.
Sprawdź `szczegoly_prognozy.csv`, źródło audytu i raporty portfela;
nie przedstawiaj zaniku pomiarów jako potwierdzenia zakończenia umowy.

Podaj czas uruchomienia, okres, liczbę oddziałów, kompletność, statusy i pliki.
Uruchamianie predykcji nie ma zmieniać artefaktów ani wywoływać `fit()`.
Zmiany predykcji sprawdź w `tests.test_branch_forecasting`, a zmiany kontroli
portfela także w `tests.test_portfolio`.
