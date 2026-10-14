---
name: pgeo-train-evaluate
description: Trenuj i oceniaj modele pobrania, oddania oraz bilansu oddziałów PGEOv1, dobierając pogodę i parametry na chronologicznej walidacji. Używaj przy treningu lub zmianie sposobu uczenia i oceny.
---

# Trening i ocena PGEOv1

Przeczytaj [kontekst](../../../docs/AI_CONTEXT.md),
[architekturę](../../../docs/ARCHITECTURE.md) oraz sekcje doboru zmiennych i
ograniczeń w [instrukcji](../../../START_BILANSY_72H.md).
Pracuj z katalogu głównego repozytorium.

## Przygotowanie

Do rzeczywistego treningu potrzebujesz pomiarów energii i **archiwum wydań**
pogody, nie tylko najnowszej prognozy. Repozytorium nie zawiera danych PGE
ani wytrenowanego na nich modelu. Jeśli brakuje wejścia, można poprawiać kod
i używać danych syntetycznych do weryfikacji programu, ale nie do oceny
rzeczywistej dokładności. Sprawdź hierarchię MDD, jednostki, czas i mapowanie.

Domyślne `--start-date` to `2024-07-01`. Faktyczny początek archiwum wymaga
potwierdzenia; trening wykorzystuje wspólne pokrycie energii i pogody.

## Uruchomienie

```bash
python -m pip install -r mdd_forecasting/requirements.txt
python -m mdd_forecasting.train --energy energia.xlsx --weather pogoda_historia.csv --artifacts-dir model_bilansu
```

Przy działającym firmowym SQL zastąp `--weather pogoda_historia.csv` przez
`--weather-sql mdd_forecasting/sql/pogoda_bilans_historia.sql`.
GUI: `python uruchom_trening_bilansow.py` lub strzałka VS Code.
Opcje sprawdź przez `python -m mdd_forecasting.train --help`.
`--tune` porównuje trzy konfiguracje rodziny, `--models auto` dołącza zainstalowany
CatBoost; `--skip-importance` pomija
obliczanie ważności. Dobierz koszt obliczeń do zadania użytkownika.

## Zachowaj poprawną ocenę

`branch_model.py` uczy dwa modele HistGradientBoosting lub CatBoost, po jednym
dla pobrania i oddania, na wspólnych danych oddziałów. Kod oddziału jest cechą;
nie mieszaj wyników OSD w jeden bilans. Treningowy horyzont domyślny to 72 h,
a symulowane origins są domyślnie co 12 h.

Dobieraj zestaw pogody i parametry wyłącznie na chronologicznej walidacji.
Oddzielny późniejszy test ocenia wybrany wariant. Nie wprowadzaj losowego
podziału szeregu ani trenowania na okresie późniejszym od ocenianego.
Zachowaj cutoff pogody względem origin całego przebiegu oraz identyczny
sposób budowania cech w uczeniu i predykcji.

Eksport nie ma czasu publikacji korekt energii. Końcowe pomiary są targetem
retrospektywnym. Domyślne embargo 72 h nie dowodzi dostępności korekty 4
w tamtej chwili; nie dodawaj lagów energii bez obsługi czasów ich dostępności.
Test służy ocenie, a ważność grup pogody interpretacji. Kolejne strojenie
po obejrzeniu testu wymaga nowego odłożonego okresu dla niezależnej oceny.

## Oceń i przekaż wynik

Sprawdź `porownanie_wariantow.csv`, `metryki_test.csv`,
`backtest_bilans.csv`, `waznosc_pogody.csv` i raporty jakości/portfela.
Porównaj ML z profilem historycznym, osobno dla oddziałów, kierunków,
bilansu oraz zakresów 1–24, 25–48 i 49–72 h. Uwzględnij pokrycie oraz
normalizację błędu po oddziałach. WAPE przy małym bilansie wymaga ostrożnej
interpretacji; podaj też MAE, RMSE i bias.
Otwórz `raport_treningu.xlsx`; szczegółowe ustawienia i interpretacja są w
[PARAMETRY_I_RAPORTY.md](../../../PARAMETRY_I_RAPORTY.md). Zmiany konfiguracji,
raportów i CatBoost sprawdź także w `tests.test_branch_training_tools`.

Przekaż `modele_oddzialow.joblib` wraz z `manifest_oddzialy.json` w folderze
artefaktów, nie jako domyślny commit danych. Zapisz zakres historii, ustawienia,
wybrany wariant i wersje bibliotek. Predykcja wymaga zgodnego scikit-learn.
Zmiany modeli/cech/zapisu sprawdź w `tests.test_branch_forecasting`, a zmiany
współdzielonych elementów również w `tests.test_mdd_forecasting`.
