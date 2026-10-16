---
name: pgeo-data-audit
description: Sprawdź dane wejściowe PGEOv1, wybór korekt energii, dopasowanie wydań pogody i obecność MDD. Używaj przy imporcie Excel/CSV, zmianie mapowania lub diagnozie braków i zmian portfela.
---

# Kontrola danych PGEOv1

Pracuj z katalogu głównego projektu. Przeczytaj
[kontekst](../../../docs/AI_CONTEXT.md) i właściwe sekcje
[instrukcji wejścia](../../../START_BILANSY_72H.md).
Ścieżki kodu poniżej są względem katalogu głównego repozytorium.

## Rozpoznaj dane przed zmianą reguł

Sprawdź nagłówki, arkusze, zakres dat, jednostki i kilka rekordów, zamiast
zakładać, że nowy eksport wygląda identycznie jak zdjęcia.
Energia ma A:I: oddział, kierunek MDM, grupa OSD, MDD, kierunek MDD, doba,
rodzaj korekty, godzina i wartość. Odczyt/normalizacja są w `io.py`, a reguły
oddziałów w `branch_data.py` wewnątrz `mdd_forecasting/`.

Jeden pomiar to oddział/MDD/kierunek/godzina UTC. Wybierz najwyższą korektę
4 > 3 > 2 > 1 > 0 przed agregacją. Równe duplikaty usuń; różne najlepsze
wersje wymagają wyjaśnienia. Nie sumuj pomiarów MDD sumarycznych i ich
składowych bez znajomości hierarchii. Nie uzupełniaj pustego oddziału poprzednim.
Kierunek wyznacza wyłącznie B: `1` czynne pobranie, `-1` czynne oddanie,
zgodnie z potwierdzeniem użytkownika 2026-10-15. E jest opisem; nie filtruj
po słowach o energii biernej i nie stosuj fallbacku z E. Zachowaj walidację
innych/brakujących kodów B i nieznanych oddziałów. Aktualna decyzja użytkownika:
ujemne i odstające pomiary I pomijamy po wyborze korekt. Ujemny bilans i B=-1
pozostają poprawne. Nie odwracaj znaku, nie sięgaj po starszą korektę i nie
sumuj tylko pozostałych MDD — wykluczony składnik zostawia sumę jako brak.
`energy_filter.py` dopasowuje górne progi na dodatniej historii przed walidacją,
stosuje je bez refitu w backteście i audycie. Nowym MDD bez referencji nie
przypisuj progów innych klientów. Czytaj raport odrzuceń, liczniki i progi;
wyniki po filtrze są warunkowe. Test: `tests.test_energy_filter`.

## Sprawdź pogodę i czas

Rozróżniaj godzinę celu od czasu dostępności wydania. Zachowaj archiwum.
W bilansach poprawne źródłowe UTC celu/wydania ma pierwszeństwo przed
CET i kluczem handlowym. `weather_time.py` raportuje rozbieżne metadane,
ale nie przesuwa UTC. Brak UTC wymaga jednoznacznej konwersji lokalnego
czasu; błędne jawne UTC i niejednoznaczny czas bez UTC zapisuje w raporcie
błędów i przerywa odczyt. Testy: `tests.test_weather_utc`.
Dla całej prognozy wybierz najnowsze wydanie z `issue_time <= origin`,
osobno dla punktu i godziny celu. Nie zastępuj tego najbliższym czasem
według wartości bezwzględnej ani filtrem globalnego ostatniego wydania.
Jeśli ingestia jest późniejsza od czasu źródła, uwzględnij rzeczywistą dostępność.

Łącz po UTC; sprawdź lokalną numerację handlową 23/25 godzin w `Europe/Warsaw`.
Aktualny trening domyślnie pomija całe doby zmiany czasu zgodnie z decyzją
użytkownika. Odczytaj `pominiete_dni_zmiany_czasu.csv`; reguła działa po
start_date i przed walidacją. W audycie stosuj flagę zapisaną w modelu oraz
ignored_dates, aby pominięte dni nie udawały braków MDD. Opcja
`--include-dst-days` przywraca poprzednią obsługę.
Test zakresu: `tests.test_dst_day_exclusion`.
Jednostki energii i promieniowania trzeba potwierdzić, nie odgadywać.
Samodzielna diagnostyka godzin została usunięta na prośbę użytkownika.
Obowiązuje wyłączenie całych dób DST, bez zmiany numeracji zwykłych godzin.
Jesienne 1–25 i kolejność wierszy nie rozstrzygają znaczenia H25. Nie uznawaj
zer za techniczne wypełnienie bez potwierdzenia. Podstawowa walidacja wejścia
pozostaje aktywna; backtest zachowuje brakujące realizacje i statusy w CSV.
Konflikty źródeł pogody rozwiązuj przez potwierdzenie właściciela/typu i filtry
`--sql-owner`/`--sql-weather-type`, nie losowe usunięcie wiersza.

## Interpretuj portfel na podstawie pomiarów

Korzystaj z `portfolio.py` oraz pomiarów zwracanych przez
`load_branch_energy(..., return_measurements=True)`. Zero jest obecnością;
brak wartości i brak rekordu nie są zerami. Próg 14 dni jest ustawialny.
Referencja końca eksportu jest osobna dla oddziału i kierunku.

Porównaj tożsamości MDD, nie tylko liczbę: odejście i pojawienie się innego
MDD mogą zachować liczebność. Uwzględnij przerwy zakończone powrotem,
opóźnienie całego eksportu i MDD będące agregatami.
Raportuj brak jako wymagający sprawdzenia. Bez danych umów nie potwierdzaj
odejścia i nie usuwaj historii ani nie skaluj prognoz automatycznie.

## Wynik pracy

Podaj zakres danych, problemy importu, pokrycie pogody, zestaw oddziałów,
zmiany obecności MDD i założenia do potwierdzenia. Oddziel brak danych od zera
i brak wiedzy o umowie od stwierdzonej zmiany pomiarów.
Po zmianie importu/czasu sprawdź `tests.test_branch_forecasting`; po zmianie
portfela `tests.test_portfolio`. Zmiana wspólnego `io.py` wymaga też sprawdzenia
starszego przepływu w `tests.test_mdd_forecasting`.
Po zmianie CSV/mapowania sprawdź `tests.test_branch_mapping`: kodowania,
separatory, ścieżki, konflikty i aliasy Łodzi `ZŁT/ZLT → ZEUD`, `ŁZE/LZE → ZEUE`.
