# Backtest bilansów — uruchomienie przez strzałkę VS Code

Otwórz **`uruchom_backtest_bilansow.py`**. To osobny test historyczny tego samego
modelu: wywołuje produkcyjne `train_branch_models()` i `predict_branches()`.
Nie kopiuje realizacji jako prognoz i nie zmienia algorytmu ani parametrów CatBoost.
Model jest uczony raz, a potem zamrożony na cały wybrany okres testu.

## Co ustawić przed uruchomieniem

1. W `uruchom_trening_bilansow.py` sprawdź `SCIEZKA_ENERGII`,
   `SCIEZKA_POGODY` i ewentualnie `SCIEZKA_MAPOWANIA`. Backtest przejmuje te
   ścieżki oraz `POGODA_SQL`. Pusta energia otwiera wybór pliku. Pusta pogoda
   wybiera ten sam SQL co trening; można podać archiwum pogody CSV/XLSX.
2. W `uruchom_backtest_bilansow.py` wpisz cztery daty w formacie `RRRR-MM-DD`:

   | Ustawienie | Znaczenie | Przykład |
   |---|---|---|
   | `TRENING_OD` | pierwsza lokalna doba historii modelu | `2024-07-01` |
   | `TRENING_DO` | ostatnia lokalna doba historii modelu, włącznie | `2025-06-27` |
   | `BACKTEST_OD` | pierwsza lokalna doba symulowanych uruchomień | `2025-07-01` |
   | `BACKTEST_DO` | ostatnia lokalna doba symulowanych uruchomień, włącznie | `2025-07-31` |

   **Przykład nie sprawdza, czy Twój eksport zawiera te daty.** Wybierz zakres
   dostępny jednocześnie w energii i archiwum wydań pogody. Historia treningowa
   musi pomieścić wewnętrzną naukę, walidację, test i embargo. Przy domyślnych
   oknach 14 + 14 dni potrzebujesz odpowiednio dłuższej historii, najlepiej wielu
   miesięcy z różnymi warunkami pogodowymi. Brak danych kończy się komunikatem.

3. `PARAMETRY` kopiuje cały słownik z obecnego treningu: `models="auto"`,
   `tune=True`, 300 iteracji, sześć wariantów pogody, te same limity próbek,
   okna wewnętrznej walidacji/testu i ziarno losowości. Parametry opisuje
   [PARAMETRY_I_RAPORTY.md](PARAMETRY_I_RAPORTY.md). Możesz je nadpisać,
   np. `PARAMETRY["threads"] = 4`. `TRENING_OD` zastępuje tutaj `start_date`.
4. Ustaw `FOLDER_BACKTESTU`, domyślnie `backtest_bilansu`. Raport i model tego
   testu trafiają do osobnego podfolderu; zwykły folder modelu jest niezależny.
5. Sprawdź interpreter VS Code. Zainstaluj zależności:

   ```powershell
   python -m pip install -r mdd_forecasting/requirements-production.txt
   ```

   Ten plik obejmuje opcjonalny CatBoost. Bez niego `auto` porównuje tylko
   HistGradientBoosting, tak samo jak zwykły trening. Zmiana dostępności/wersji
   bibliotek może zmienić wybrany model; wersje są zapisane w manifeście.

6. Kliknij **▶ Run Python File** albo uruchom:

   ```powershell
   python uruchom_backtest_bilansow.py
   ```

## Jakie godziny są oceniane

Domyślnie symulowane uruchomienia są co 12 godzin, na siatce UTC 00:00/12:00,
wybrane według lokalnych dat `BACKTEST_OD/DO` w `Europe/Warsaw`.
Każde przewiduje następne **72 pełne godziny**, tak samo jak program prognozy.
**Koniec backtestu dotyczy daty uruchomienia, nie ostatniej prognozowanej godziny.**
W przykładzie prognozy z 31 lipca sięgają 3 sierpnia; realizacje i archiwum pogody
powinny sięgać do ostatniego celu zapisanego w `zakres_backtestu.json`.

Przed pierwszą prognozą wymagany jest odstęp `label_delay_hours` od końca doby
`TRENING_DO` (domyślnie 72 h). Program odrzuca nakładające się zakresy.
Wewnętrzne strojenie, test i końcowy refit odbywają się **wyłącznie wewnątrz
zakresu treningowego**. Raporty tego wcześniejszego podziału są w `trening/`.
Żaden cel z zewnętrznego backtestu nie wraca do `fit()`, wyboru pogody ani profilu.
Nie ma okresowych refitów w trakcie backtestu: odwzorowuje on korzystanie
z jednego zapisanego modelu.

Pogoda dla każdego celu musi mieć czas wydania nie późniejszy niż dany origin.
Potrzebne jest **archiwum wydań**, nie tylko aktualna prognoza lub obserwowana
pogoda historyczna. Do treningu nie trafi przyszłe wydanie. Jak dotychczas,
nie znamy czasów publikacji korekt energii: ocena wykorzystuje najlepsze wersje
z obecnego eksportu i ma charakter retrospektywny. Lagi energii są wyłączone.

Całe lokalne doby zmiany czasu (23 i 25 h) są domyślnie pomijane w historii
i ocenie. Nie zmieniamy numeracji H ani nie tworzymy sztucznych godzin.
Prognoza nadal ma pełny horyzont; jej cele DST zostają w CSV ze statusem
`POMINIETA_DOBA_ZMIANY_CZASU`. Braki realizacji poza tymi dniami są jawne,
wliczane do pokrycia i nie są zerami. Zero energii jest poprawnym pomiarem.
Usunięto dawny samodzielny skrypt diagnostyki godzin i jego raporty.
Podstawowa walidacja kolumn, kierunku B, korekt i czasu pozostaje aktywna.

## Co otworzyć rano / na spotkaniu

`backtest_bilansu/OSTATNI_BACKTEST.txt` wskazuje dokładny podfolder ostatniego
uruchomienia. Otwórz w nim:

| Plik | Zastosowanie |
|---|---|
| `status_backtestu.json` | stan końcowy procesu, liczba gotowych uruchomień; sprawdź jako pierwszy |
| `raport_backtestu.html` | samodzielny raport w przeglądarce, bez Internetu; wybór oddziału, uruchomienia, bilansu/pobrania/oddania, wykresy realizacji/ML/profilu i tabela błędów; druk do PDF |
| `raport_backtestu.xlsx` | błędy oddziałów, horyzontów, dni, godzin i warunków pogody; największe pomyłki, pokrycie, parametry, trzy wykresy dla każdego oddziału |
| `podsumowanie_backtestu.txt` | krótki opis zakresu, pokrycia i wyników bilansu |
| `backtest_bilans.csv` | wszystkie bilanse rzeczywiste i przewidywane oraz profil odniesienia, origin i horyzont |
| `szczegoly_backtestu.csv` | osobne kierunki, wydanie pogody i statusy każdej prognozy |
| `przebieg_ciagly.csv` | po jednej, najnowszej prognozie dla każdej godziny/oddziału; źródło wykresu ciągłego |
| `trening/raport_treningu.xlsx` | wybór modelu, konfiguracji i pogody oraz ważność grup pogody na wcześniejszym wewnętrznym teście |
| `trening/modele_oddzialow.joblib` i `manifest_oddzialy.json` | dokładny model użyty do tego backtestu i jego wersje bibliotek |
| `backtest.log`, `postep_strojenia.csv`, `przebiegi/` | postęp, ukończone próby strojenia i bilanse kolejnych uruchomień |

MAE i RMSE są w jednostce wejściowej energii; mniejsze wartości oznaczają
mniejszy błąd. Bias dodatni oznacza zawyżanie. WAPE `0,10` to `10%`, ale przy
małym bilansie może być niestabilny. Patrz też na pokrycie, P90/P95, kierunki
i horyzonty 1–24, 25–48, 49–72 h. Porównanie ML z profilem jest liczone na
wspólnych dostępnych wierszach.

Statystyki oceniają wszystkie pary `origin/cel`: ta sama rzeczywista godzina
może być prognozowana wielokrotnie z różnym wyprzedzeniem. `n_godzin` rozróżnia
unikalne godziny. Wykres ciągły pokazuje najnowszą prognozę dla celu i nie jest
osobnym wynikiem jakości z tabeli. HTML ogranicza wybór do
`max_html_origins` równomiernie wybranych uruchomień; ciągły widok obejmuje całość.
Excel pokazuje do 20 000 wierszy tabeli i pierwsze 5000 punktów wykresu oddziału;
pełne dane pozostają w CSV. Nie obiecujemy określonej dokładności przed oceną
na danych PGE. Wyniki testów programu na danych syntetycznych nie są takim wynikiem.

## Limit nocnych obliczeń i wznowienie

`WYKONANIE["max_runtime_hours"] = 10` ogranicza cały proces, włącznie z odczytem,
SQL, treningiem i raportami. Start o 20:00 oznacza zatrzymanie około 06:00,
z niewielkim czasem potrzebnym na zakończenie procesu. **Limit nie gwarantuje,
że pełny model zdąży się nauczyć.** Czas zależy od CPU, RAM, danych i SQL.
Laptop uruchamiający skrypt musi być włączony i nie może się uśpić.

Program nie obcina po cichu grida ani liczby drzew. Po każdej zakończonej próbie
zapisuje jej wynik. Po pełnym treningu zapisuje model. Raport częściowy powstaje
po pierwszej prognozie, następnie co `report_every_origins` uruchomień i na końcu.
Po przekroczeniu limitu kod zakończenia to **124**, status to `LIMIT_CZASU`,
`kompletny=false`. Wcześniej zapisany raport może obejmować mniej uruchomień niż
liczba ukończonych plików w `przebiegi/`; stan raportu i jego zakres są jawne.
Jeżeli limit przerwie samo uczenie, raportu jakości jeszcze nie będzie.

Ponownie uruchom ten sam skrypt z `resume=True`. Identyczne wejścia, kod,
ustawienia i wersje bibliotek wykorzystają zapisane wyniki strojenia albo gotowy
model. Przerwana próba jest powtarzana; obowiązkowe refity modelu muszą się
ukończyć. Prognozy i raporty są przeliczane do nowego podfolderu, bez mieszania
z poprzednim. Cache jest w `backtest_bilansu/cache/`; dla SQL wznowienie używa
tego samego zapisanego archiwum pogody. `resume=False` pobiera je ponownie
i rozpoczyna nowe uczenie. Nie zmieniaj ręcznie cache ani plików joblib.

Kod 0 oznacza ukończony zakres bez wykrytych braków. Kod 2 oznacza ukończony
zakres z brakami realizacji/prognozy lub częściową pogodą; pokaż to z wynikami.
Kod 1 oznacza błąd — opis jest w `blad_backtestu.txt`. Kod 124 oznacza limit.
Wyniki, rzeczywiste dane, modele i cache są lokalne i nie trafiają do Git.
