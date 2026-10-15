# Prognoza bilansów PGE na 72 godziny

Program przygotowuje pobranie, oddanie i bilans osobno dla każdego oddziału.
Bilans to **pobranie minus oddanie**. Jednostki są takie same jak w źródłowym
pliku energii. Na podstawie samych zdjęć nie potwierdzono, czy jest to kWh czy MWh.

## Dwa programy

1. **`uruchom_trening_bilansow.py`** — odczytuje historię energii, pobiera archiwum
   prognoz pogody z SQL, porównuje zestawy zmiennych i zapisuje modele oraz raporty.
2. **`uruchom_prognoze_bilansow.py`** — wczytuje zapisane modele i aktualną pogodę.
   Tworzy prognozę na 72 kolejne pełne godziny od następnej godziny zegarowej.
   Możesz uruchamiać go kilka razy dziennie. Nie wykonuje uczenia.

Oba pliki można uruchomić strzałką ▶ w VS Code na komputerze służbowym.
Na początku pliku treningowego wpisz `SCIEZKA_ENERGII`, `FOLDER_MODELU` oraz
ustawienia w `PARAMETRY`. Pusta ścieżka energii otwiera okno wyboru.
W pliku prognozy ustaw ten sam folder modelu, źródło pogody i opcjonalny Excel
do kontroli klientów. Pusta ścieżka energii oznacza kontrolę z historii treningu.
Wyniki prognoz powstają w osobnych folderach `prognozy`.
Pełny opis: **[PARAMETRY_I_RAPORTY.md](PARAMETRY_I_RAPORTY.md)**.

## Uruchomienie na komputerze służbowym

Rozpakuj paczkę i otwórz cały folder w VS Code. Zainstaluj biblioteki:

```powershell
python -m pip install -r mdd_forecasting\requirements.txt
```

Potrzebny jest Microsoft **ODBC Driver 17 for SQL Server**, jak w pokazanym
działającym programie. Dostęp do bazy wymaga firmowej sieci/VPN oraz uprawnień
aktualnego konta Windows. Połączenie korzysta z:

```text
DRIVER={ODBC Driver 17 for SQL Server};
SERVER=MISDWHPRD.GKPGE.PL;
DATABASE=PGESA_MarketAnalytics;
Trusted_Connection=yes;
```

Kwerenda jest w osobnym pliku
`mdd_forecasting/sql/pogoda_bilans_historia.sql`. Python odczytuje ten plik i
wykonuje go przez `pyodbc` oraz `pandas.read_sql_query`, zamykając połączenie
również przy błędzie. Parametry przekazuje oddzielnie, bez podmieniania tekstu SQL.
Serwer/bazę/sterownik można zmienić przez opcje CLI. Istniejąca zmienna
`MDD_SQL_CONNECTION_STRING` jest opcjonalnym nadpisaniem konfiguracji.

Zdjęcie wyników SQL pokazuje typ `klient zagregowany`. Dlatego nowa kwerenda
nie narzuca typu `Open Meteo` ani właściciela `PGESA`. W razie różnych prognoz
dla tej samej lokalizacji ustaw `--sql-owner` i `--sql-weather-type`.
Sprzecznych wydań o tym samym czasie źródła program nie wybiera losowo.

## Excel i CSV z energią

Kolumny A:I są odczytywane w kolejności:

| Kolumna | Znaczenie |
|---|---|
| A | nazwa oddziału MDM |
| B | jedyne źródło kierunku: 1 czynne pobranie, -1 czynne oddanie |
| C | grupa OSD MDM |
| D | nazwa MDD |
| E | opis źródłowy MDD; nie określa kierunku ani nie filtruje pomiarów |
| F | doba handlowa |
| G | rodzaj danych |
| H | godzina handlowa |
| I | ilość energii |

Obsługiwany jest CSV otwierany w Excelu, XLSX i XLSM. Arkusz ze zdjęcia
`dane_po_transpozycji` oraz arkusze `Dane_01`, `Dane_02`, ... są rozpoznawane.
Jeśli skoroszyt ma jeden arkusz, jest on odczytywany niezależnie od nazwy.
Dla innych nazw w wieloarkuszowym pliku służy `--sheet-regex`.

Jeden pomiar identyfikuje oddział, MDD, kierunek, doba i godzina. Spośród korekt
wybierana jest jedna najwyższa wersja **4 > 3 > 2 > 1 > 0**. Identyczne duplikaty
nie są sumowane. Różne wartości tego samego pomiaru i tej samej najlepszej wersji
przerywają uruchomienie i wymagają wyjaśnienia eksportu.
Jeżeli pomiar MDD nie ma wartości, suma jego oddziału w tej godzinie pozostaje
brakiem. Kierunek oddania bez pomiarów nie jest automatycznie uznawany za zero.

Zgodnie z potwierdzeniem użytkownika 15 października 2026 kierunek ustala
wyłącznie kolumna B: dokładnie `1` to czynne pobranie, a `-1` czynne oddanie.
Opisy E, także zawierające słowo „bierna”, puste lub sprzeczne z B, nie wpływają
na kierunek ani dopuszczenie pomiaru. Program nie wykorzystuje E jako fallbacku.
Inne lub brakujące wartości B zatrzymują import z liczbą problemów i przykładami
rekordów, w tym arkuszem, numerem wiersza i oryginalnym kodem B.
Różne opisy E nie tworzą osobnych pomiarów: te same najlepsze wersje o równej
wartości są deduplikowane, a sprzeczne wartości są zgłaszane z przykładami.
Ujemne wartości i nieznane oddziały pozostają błędami.
Puste A nie jest uzupełniane poprzednim oddziałem.

Domyślny trening, zgodnie z decyzją użytkownika, pomija całe doby 23/25 h
w kalendarzu `Europe/Warsaw`, po `--start-date`, ale przed walidacją i agregacją.
Dotyczy to obu zmian czasu w każdym roku historii. Nie wyznacza się tych dni
na podstawie liczby pomiarów ani H25. Niepoprawna data F pozostaje do wyjaśnienia.
Zestawienie dat i liczby rekordów zawiera `pominiete_dni_zmiany_czasu.csv`
i arkusz `Pominiete_doby`. Źródłowy plik pozostaje zachowany.
Wyłączone doby nie trafiają do treningu, walidacji ani testu, a pomiary sąsiednich
dat zachowują swoje UTC. Audyt portfela pomija te daty przy liczeniu braków MDD.
Opcja CLI `--include-dst-days` przywraca wcześniejszą obsługę tych dób.
Reguła zostaje zapisana w modelu i jest stosowana także do aktualnego eksportu
energii w audycie predykcji. [Szczegóły](PARAMETRY_I_RAPORTY.md#kontrole-danych-i-granice-oceny).

Mapowanie można edytować w `mdd_forecasting/config/oddzial_miasto.csv`:

| Oddział | Punkt pogody |
|---|---|
| BIA | Białystok |
| LUB, alias LBU | Lublin |
| RZE, alias RZ | Rzeszów |
| SKO | Skarżysko-Kamienna |
| WAW | Warszawa |
| ZAM | Zamość |
| ZEUD, alias ZŁT/ZLT | Łódź |
| ZEUE, alias ŁZE/LZE ze zdjęcia | Łódź |

Użytkownik potwierdził 15 października 2026: `ZŁT` to Łódź teren (`ZEUD`),
a `ŁZE` to Łódź miasto (`ZEUE`). Oba oddziały korzystają z pogody Łodzi,
ale ich pomiary i bilanse pozostają osobne. Normalizacja usuwa znaki diakrytyczne
tylko w kluczach porównania; nazwy w pliku zachowują polskie znaki.

Odczyt mapowania obsługuje UTF-8 (także BOM), Windows-1250 oraz UTF-16 z BOM,
z separatorem przecinek, średnik lub tabulator. Zalecany zapis to CSV UTF-8.
Po odczycie program wypisuje pełną ścieżkę do użytego pliku i kodowanie.
Domyślna ścieżka jest liczona od katalogu pakietu, niezależnie od katalogu
roboczego VS Code. Własny plik wybierz przez `SCIEZKA_MAPOWANIA` na początku
`uruchom_trening_bilansow.py` lub `--mapping` w CLI. Spacje wokół pól i wielkość
liter w nagłówkach/statusach są normalizowane. Dozwolone statusy to `active`
i `inactive`; aktywne wpisy wymagają kodu i miasta. Sprzeczne wpisy są błędem.
Nieznany kod zatrzymuje import z informacją o pliku mapowania, kluczu po
normalizacji oraz przykładowym arkuszu/wierszu eksportu; nie jest pomijany.

Kompletność mapowania należy potwierdzić na pełnej liście oddziałów
z rzeczywistego eksportu. Do prognozy wchodzą wyłącznie oddziały,
które mają historię obu kierunków. Jeśli eksport zawiera równocześnie MDD
sumaryczne i ich składowe, przed uczeniem trzeba usunąć to nakładanie portfeli.
Program usuwa wersje tego samego MDD, ale nie zna hierarchii między różnymi MDD.

## Klienci pojawiający się i znikający z danych

Bilans jest osobny dla każdego OSD/oddziału. Pomiary MDD są najpierw sumowane
osobno dla pobrania i oddania w tym oddziale. Model wspólny dla oddziałów nie
łączy ich wyników w jeden bilans.

Źródłem wiedzy o portfelu jest obecnie wyłącznie historia pomiarów. Dlatego
kontrola nie nadaje statusów aktywnej ani zakończonej umowy. Wykazuje:

- pierwszy i ostatni dzień pomiarów każdego MDD w oddziale i kierunku;
- MDD nieobecne na końcu eksportu, w tym dłuższe braki;
- przerwy w pomiarach i ich późniejszy powrót;
- dobowe zmiany zestawu MDD, także wymianę klientów przy tej samej liczbie MDD;
- różnicę między zestawem na końcu aktualnego eksportu a tym z treningu.

Domyślny próg dłuższego braku to **14 dni**, ustawialny przy treningu przez
`--portfolio-grace-days`. Referencją jest najnowsza doba pomiarów osobno dla
oddziału i kierunku. Opóźnienie całego Lublina wobec Białegostoku nie oznacza,
że wszyscy klienci lubelscy zniknęli. Stary eksport otrzymuje jednak oznaczenie
przy prognozie, porównując jego czas z chwilą uruchomienia.

Wartość zero oznacza istniejący pomiar. Brak wartości i brak całego rekordu
mają inne znaczenie. Zanik pomiarów może wynikać z odejścia, opóźnienia
operatora, zmiany identyfikatora lub niepełnego eksportu. Jeżeli MDD jest
agregatem wielu klientów, zmian umów wewnątrz tego agregatu nie można
zidentyfikować samymi tymi kolumnami.

Kontrola nie usuwa automatycznie dawnych MDD z historii i nie przelicza
prognozy proporcjonalnie do liczby klientów. Ta sama liczba MDD nie oznacza
takiego samego wolumenu energii. W prognozie dodano `status_portfela` oraz
osobne statusy dla pobrania i oddania. Zmiana lub dłuższe braki oznaczają
**PORTFEL_DO_WERYFIKACJI**. Liczbowa prognoza pozostaje wynikiem zapisanego
modelu, nie prognozą automatycznie dostosowaną do potwierdzonych odejść.
Po potwierdzeniu zmiany trzeba dobrać sposób dostosowania modelu do portfela.

Bez nowego eksportu program korzysta ze stanu pomiarów zapisanego podczas
nauki. Nie może wykryć zmian, które nastąpiły później i nie zostały dostarczone.
Aktualny eksport można podać do predykcji jako `--energy`; jest używany tylko
do kontroli, bez uczenia ani zmieniania modeli.

## Wybór prognozy pogody

`dataGodzinaUTC/CET` mówi, jakiej godziny dotyczy pogoda. `czasDanychZrodlaUTC/CET`
mówi, kiedy prognoza była dostępna, zgodnie z opisem użytkownika.
Przykładowo, przy uruchomieniu o 10:00 wszystkie 72 godziny otrzymują najnowsze
wydanie z czasem źródła **nie późniejszym niż 10:00**. Wydanie z 11:00 nie trafia
do tej prognozy, nawet jeśli dotyczy jutrzejszego południa.

Archiwum wydań jest potrzebne do uczenia. Nie używamy `ABS(DATEDIFF(...))`,
`GETDATE()` jako historycznego cutoffu ani globalnych flag ostatniej prognozy.
Jeśli eksport już usunął wcześniejsze wydania, nie da się odtworzyć nimi
historycznego testu 72 h. Wybór pogody odbywa się po UTC, a wyniki mają także
dobę i godzinę handlową. Dwa łódzkie oddziały współdzielą pogodę.

Pogoda jest godzinowa. Kwerenda pobiera `pelnaGodzina=1`, a odczyt z pliku
odrzuca czasy między pełnymi godzinami. Numeracja przyjęta zgodnie z wcześniejszym
formatem hurtowni: 1 to lokalna 00:00; dodatkowa jesienna lokalna 02:00 zimowa
ma numer 25. Nie jest to północ następnego dnia. Wiosenna nieistniejąca 02:00
nie może być zwykłym rekordem godziny 3. Jeśli Excel stosuje inną numerację,
trzeba dostosować ją do faktycznych kluczy hurtowni.

W zgłoszeniu z 15 października 2026 wystąpiła H3 w dniu `2024-03-31`.
Znaczenie tej etykiety w eksporcie pozostaje do sprawdzenia. Uruchom
`uruchom_diagnostyke_godzin.py` strzałką ▶, aby porównać wzorce bez SQL i
treningu. Sam jesienny zestaw 1–25 pasuje do obu wariantów i nie wyjaśnia
znaczenia H25. [Instrukcja diagnostyki](DIAGNOSTYKA_GODZIN.md).

Aktualne obejście wyłącza całe doby zmiany czasu z historii energii; nie zmienia
interpretacji pozostałych godzin ani zapytań pogodowych. Prognoza nadal liczy
72 rzeczywiste godziny, również przy przejściu CET/CEST. Wynik testu jakości
nie opisuje dokładności w wyłączonych dobach.

Nie potwierdzono na samych zdjęciach, czy czas źródła zawsze jest czasem
udostępnienia w bazie. Jeśli istnieje późniejszy czas ingestii, on również musi
ograniczać dostępność prognozy.

## Jak wybierane są zmienne i parametry

Dwa modele regresji gradientowej uczą się sumy pobrania i sumy oddania.
Współdzielą dane oddziałów, a kod oddziału jest cechą. Bilans jest ich różnicą
i może być ujemny. Pobranie i oddanie są ograniczane od dołu zerem.

Porównywane są: sam kalendarz, temperatura, samo promieniowanie, temperatura
z promieniowaniem, podstawowa pogoda oraz pełny dostępny zestaw. Uwzględniane są
temperatura, promieniowanie całkowite i bezpośrednie, wiatr, zachmurzenie,
opady konwekcyjne, widoczność, albedo i śnieg. Kierunek wiatru ma cechy sin/cos.
Całkowicie puste kolumny ze zdjęć nie są sztucznie wypełniane zerami jako nowe
informacje. Jednostki promieniowania i pozostałych danych pozostają źródłowe.

Dobór wykorzystuje osobne chronologiczne okno walidacyjne. Wynik jest średnim
błędem znormalizowanym po oddziałach, aby duży oddział nie zdominował wyboru.
Opcja `--tune` porównuje trzy zestawy parametrów każdej rodziny.
`--models auto` dołącza CatBoost, jeśli jest zainstalowany; bez niego jawnie
pozostaje HistGradientBoosting. Wariant i parametry wybiera wyłącznie walidacja.
Każdy oddział/kierunek musi mieć odpowiednie pokrycie w walidacji i teście.
Późniejsze, osobne okno testowe służy ocenie wybranego modelu oraz grupowej
ważności pogody. Test nie wybiera zestawu cech. Końcowe modele są następnie
uczone na całej dostępnej wspólnej historii, do limitu najnowszych wierszy.

Trening symuluje uruchomienie co 12 godzin z prognozą 72 h. Częstotliwość jest
ustawialna i nie ogranicza częstotliwości faktycznego uruchamiania predykcji.
Raport błędu obejmuje każdy oddział i zakresy 1–24, 25–48 oraz 49–72 godzin.
Punktem odniesienia jest profil mediany dla oddziału, dnia tygodnia i godziny,
wyznaczony wyłącznie na części historii poprzedzającej test.

**Ograniczenie danych:** eksport A:I nie ma czasu udostępnienia korekt. Ocena
jest retrospektywna na najlepszych dostępnych teraz wersjach pomiarów, a nie
pełnym odtworzeniem historycznej dostępności danych u operatora. Przyjęto
ustawialne embargo 72 h dla dat realizacji, lecz nie zastępuje ono czasu
publikacji korekty 4 po kilku miesiącach. Z tego powodu lagi energii są
wyłączone. Można je dodać po otrzymaniu historii dostępności kolejnych korekt.

## Wyniki

Folder treningu zawiera:

- `raport_treningu.xlsx` — opis metryk, wybór modeli, błędy godzinowe/dobowe,
  warunki pogody, największe pomyłki, pokrycie danych i wykresy;
- `modele_oddzialow.joblib` — modele pobrania i oddania wraz ze schematem cech;
- `manifest_oddzialy.json` — ustawienia, mapowanie i wersje bibliotek;
- `porownanie_wariantow.csv` — wpływ zestawów pogody oraz parametrów na walidację;
- `waznosc_pogody.csv` — wzrost błędu po zaburzeniu poszczególnych grup pogody
  na odłożonym teście; większa dodatnia wartość oznacza większą przydatność;
- `metryki_test.csv` — MAE, RMSE, bias i WAPE, dla kierunków i bilansu;
- `backtest_bilans.csv` — prognozy, realizacje i profil odniesienia;
- `kontrola_energii.csv`, `kontrola_modelu.csv` — korekty, braki oraz założenia;
- `pominiete_dni_zmiany_czasu.csv` — kalendarzowo wyłączone doby 23/25 h i liczby rekordów;
- `historia_oddzialow.csv` — przygotowane sumy godzinowe.
- `portfel_mdd.csv` — daty obecności, przerwy i status każdego MDD;
- `przerwy_pomiarow_mdd.csv` — okresy braku pomiarów zakończone powrotem;
- `zmiany_portfela_dzienne.csv` — nowe/brakujące MDD i liczebność każdego dnia;
- `podsumowanie_portfela.csv` — zestawienie oddziałów i kierunków.

Predykcja zapisuje `prognoza_bilansu_72h.csv` oraz `szczegoly_prognozy.csv`.
Przy ośmiu oddziałach jest **576 wierszy bilansu**: 8 × 72. CSV ma separator `;`,
przecinek dziesiętny i UTF-8 BOM, aby dało się go otworzyć w polskim Excelu.
Godziny UTC zawierają offset czasu; klucze doba/godzina handlowa są lokalne.
Brak wymaganej pogody powoduje pustą prognozę oraz jawny status, nie pozorne zero.
Częściowy brak wybranych cech daje `PROGNOZA_NIEPELNA_POGODA`, listę brakujących
cech w szczegółach i kod zakończenia 2, także gdy model zwrócił wynik liczbowy.

## Terminal i praca bez połączenia z SQL

Trening z eksportem pogody:

```powershell
python -m mdd_forecasting.train --energy dane_po_transpozycji.csv --weather pogoda_historia.csv --artifacts-dir model_bilansu
```

Trening z firmową bazą:

```powershell
python -m mdd_forecasting.train --energy energia.xlsx --weather-sql mdd_forecasting/sql/pogoda_bilans_historia.sql --artifacts-dir model_bilansu
```

Predykcja z aktualnym eksportem pogody:

```powershell
python -m mdd_forecasting.predict --artifacts-dir model_bilansu --weather pogoda_aktualna.csv --output-dir prognoza_dzisiaj
```

Predykcja z kontrolą nowych pomiarów MDD, bez ponownego uczenia:

```powershell
python -m mdd_forecasting.predict --artifacts-dir model_bilansu --weather pogoda_aktualna.csv --energy aktualna_energia.csv --output-dir prognoza_dzisiaj
```

W `szczegoly_prognozy.csv` kolumna `zrodlo_kontroli_portfela` wskazuje,
czy kontrola opiera się na aktualnym eksporcie czy historii treningu.
Starsze modele bez danych do tej kontroli mają status
`BRAK_KONTROLI_PORTFELA_W_MODELU`; nowy trening zapisuje te informacje.

Predykcja z SQL, dowolną liczbę razy dziennie:

```powershell
python -m mdd_forecasting.predict --artifacts-dir model_bilansu --weather-sql mdd_forecasting/sql/pogoda_bilans_historia.sql --output-dir prognoza_dzisiaj
```

Parametr `--as-of "2026-10-13T10:30:00+02:00"` pozwala jawnie określić origin.
Nie może on poprzedzać końca historii użytej do uczenia. `--horizon-hours` może
skrócić horyzont; większy niż treningowy wymaga ponownego treningu.
Przy brakach predykcja kończy się kodem 2 i pozostawia CSV ze statusami.

Modele przenoś między komputerami razem z manifestem. Wersja scikit-learn
przy predykcji musi odpowiadać wersji treningu. Dołączone pliki kodu nie
zawierają wytrenowanego modelu na danych PGE: otrzymano tylko zdjęcia.
Testy na sztucznych danych potwierdzają działanie programu, nie jego dokładność
dla rzeczywistego portfela. Firmowego SQL-a nie sprawdzono w tej sesji.

## Źródła reguł technicznych

Regresja i obsługa braków: [dokumentacja scikit-learn](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html).
Interpretacja permutacyjnej ważności: [scikit-learn](https://scikit-learn.org/stable/modules/permutation_importance.html).
Kalendarz obejmuje Wigilię od 2025 r. zgodnie z [ustawą z 6 grudnia 2024 r.](https://eli.gov.pl/api/acts/DU/2024/1965/text.html).
