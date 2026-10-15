# Kontekst PGEOv1 dla kolejnego AI

Ten dokument przekazuje ustalenia projektu. Szczegóły uruchamiania są w
[START_BILANSY_72H.md](../START_BILANSY_72H.md), a moduły opisuje
[ARCHITECTURE.md](ARCHITECTURE.md). Sprawdzaj aktualny kod przy zmianie zachowania.

## Oczekiwany wynik

Użytkownik potrzebuje osobnej godzinowej prognozy dla każdego oddziału/OSD,
na najbliższe **72 godziny**. Program predykcyjny może działać kilka razy
dziennie. Trening ma być oddzielną operacją, która zapisuje modele do ponownego użycia.
Docelowe uruchomienie to Python/VS Code na służbowym Windows, z odrębną
kwerendą SQL odczytywaną i wykonywaną z Pythona tak jak w istniejących programach.
Tryb z eksportem pogody pozwala pracować bez SQL.

Istnieją dwa modele regresji: pobranie i oddanie. To nie jest osiem oddzielnych
par modeli: oba uczą się na danych różnych oddziałów, z kodem oddziału jako cechą.
Wynik każdego oddziału jest osobny. Bilans jest różnicą prognoz obu kierunków.
Pobranie i oddanie są nieujemne; bilans może być ujemny, np. przy przewadze oddania z PV.

## Dane energii

Układ A:I pochodzi z opisu użytkownika i zdjęć eksportu:

| Kolumna | Znaczenie i zastosowanie |
|---|---|
| A | oddział MDM; przypisanie bilansu i punktu pogody |
| B | jedyne źródło kierunku energii czynnej: `1` pobranie, `-1` oddanie |
| C | grupa OSD MDM; informacja źródłowa, nie klucz ośmiu oddziałów |
| D | nazwa MDD; identyfikator serii, czasem agregatu klientów |
| E | opis źródłowy MDD; nie wpływa na kierunek ani filtrowanie |
| F | doba handlowa, data |
| G | rodzaj danych/korekta; preferencja `4 > 3 > 2 > 1 > 0` |
| H | godzina handlowa |
| I | ilość energii; jednostka pozostaje źródłowa |

Pomiar identyfikuje **oddział + MDD + kierunek + godzina UTC**. Wybór korekt
następuje przed sumowaniem. Równe duplikaty najlepszej wersji liczymy raz;
różne wartości w tej samej najlepszej wersji powodują błąd.
Brak wartości u obecnego MDD pozostawia niepełną sumę oddziału.
Braku rekordu całego MDD nie da się wykryć samą sumą; temu służy kontrola portfela.

Osiem kodów kanonicznych: `BIA`, `LUB`, `RZE`, `SKO`, `WAW`, `ZAM`, `ZEUD`, `ZEUE`.
Mapowanie miast i aliasy są w
[oddzial_miasto.csv](../mdd_forecasting/config/oddzial_miasto.csv).
`LBU → LUB`, `RZ → RZE`, `ZŁT/ZLT → ZEUD`, `ŁZE/LZE → ZEUE`;
dwa łódzkie oddziały korzystają z Łodzi.
MDD nie należy utożsamiać automatycznie z jednym klientem lub jedną umową.

## Pogoda i dostępność informacji

Źródło: widok `[PGESA_MarketAnalytics].[wa].[vPogodaPrognoza]`.
`dataGodzinaUTC/CET` to czas, którego dotyczy pogoda.
`czasDanychZrodlaUTC/CET` jest obecnie traktowany jako czas jej dostępności,
zgodnie z opisem użytkownika. Jeżeli ingestia jest późniejsza, ona również
musi ograniczać dostępność danych.

Dla uruchomienia o 10:30 i celu jutro o 12:00 wybieramy najnowsze wydanie dla
tego celu dostępne do 10:30. Wydanie z 11:00 jest wykluczone. Ten sam cutoff
10:30 obowiązuje dla wszystkich 72 godzin; nie przesuwa się wraz z horyzontem.
Archiwum musi zachować poprzednie wydania do testów historycznych.

Zmienne obejmują temperaturę, promieniowanie całkowite i bezpośrednie,
wiatr, zachmurzenie, opad konwekcyjny, widoczność, albedo i warstwę śniegu.
Kierunek wiatru jest kodowany przez sin/cos. Zestaw pogody wybierany jest na
walidacji. Ważność grup zmiennych pomaga interpretować model, nie dowodzi przyczynowości.

Wszystkie połączenia czasowe używają UTC. Lokalny kalendarz jest polski.
Przyjęta numeracja handlowa: `1 = 00:00`, a jesienna dodatkowa zimowa `02:00`
to godzina `25`. Wiosenna nieistniejąca `02:00` jest odrzucana. Konwencję
trzeba porównać z rzeczywistym eksportem i metadanymi hurtowni przed wdrożeniem.

## Zmiany portfela

Użytkownik ma **tylko historię pomiarów energii**, bez dat zawarcia i zakończenia
umów. Audyt pokazuje pierwszą/ostatnią obecność, przerwy, powroty i zmianę
zestawu MDD. Zero liczy się jako obecny pomiar.
Domyślne 14 dni to ustawialny próg dłuższego braku, nie dowód odejścia.
Referencja jest liczona osobno dla oddziału i kierunku, aby opóźnienie całego
oddziału nie wyglądało jak odejście wszystkich jego klientów.

`status_portfela` jest osobny od statusu dostępności prognozy. Model może zwrócić
liczbę i równocześnie `PORTFEL_DO_WERYFIKACJI`. Aktualny plik `--energy` przy
predykcji służy tylko audytowi; nie aktualizuje modeli i nie zmienia wolumenu.
Bez niego kontrola korzysta z historii zapisanej przy treningu.
Zmiany umów wewnątrz sumarycznego MDD mogą pozostać niewidoczne.

## Ocena i aktualny stan

Implementacja używa `HistGradientBoostingRegressor` i opcjonalnego CatBoost,
chronologicznej walidacji i oddzielnego testu. `--models auto` porównuje dostępne
rodziny. Porównuje zestawy pogody i opcjonalnie trzy konfiguracje rodziny przez
`--tune`. Raportuje oddziały, dokładne horyzonty, zakresy 1–24, 25–48 i 49–72 h,
godziny, dni, miesiące, warunki pogody i największe błędy. Excel zawiera opis metryk.
Profil mediany oddział/dzień tygodnia/godzina jest modelem odniesienia.
Po wyborze końcowe modele są uczone na dostępnej wspólnej historii do limitu wierszy.

Eksport energii nie ma czasu udostępnienia kolejnych korekt. Dlatego ocena
jest retrospektywna na najlepszych aktualnych pomiarach, a domyślne embargo
72 h nie odtwarza kilkumiesięcznej publikacji korekty 4. Lagi energii są wyłączone.
Nie opisuj takiego testu jako pełnej symulacji historycznej dostępności pomiarów.

Stan przy przekazaniu projektu 13 października 2026:

- kod treningu, predykcji, audytu portfela i raportów jest w repozytorium;
- 32 testy przeszły na danych syntetycznych przed dodaniem tej dokumentacji;
- otrzymano zdjęcia struktur danych i przykładów, bez rzeczywistego pliku pomiarów;
- nie wykonano treningu na portfelu PGE i nie sprawdzono firmowego SQL-a;
- repozytorium jest prywatne; samo przechowywanie kodu na GitHubie nie uruchamia modelu w chmurze.

## Do potwierdzenia na rzeczywistych danych

| Temat | Dlaczego ma znaczenie |
|---|---|
| kWh/MWh i jednostki pogody | nie wolno wymyślać przeliczeń ani etykiet |
| kody oddziałów, aliasy i godziny 23/25 | poprawne przypisanie miejsca i czasu |
| hierarchia MDD sumarycznych i składowych | zapobieganie podwójnemu liczeniu energii |
| faktyczny początek archiwum pogody: lipiec/sierpień 2024 | `--start-date` domyślnie 2024-07-01; trening wykorzystuje wspólne pokrycie |
| moment dostępności pogody i ewentualna ingestia | uniknięcie informacji z przyszłości |
| właściciel/typ danych pogodowych | zdjęcie pokazuje `klient zagregowany`; filtry SQL są opcjonalne |
| zakres i opóźnienie eksportów energii | interpretacja przerw i zmian portfela |

Po otrzymaniu plików najpierw sprawdź te założenia, potem wykonaj trening i oceń
wyniki na danych PGE. Nowe decyzje zapisuj tutaj z rozróżnieniem potwierdzenia
od założenia; nie utrwalaj w dokumentacji lokalnych haseł ani tokenów.

Zmiany 14 października 2026: ścieżki i parametry są edytowalne na początku
dwóch plików `uruchom_*_bilansow.py`; opis w
[PARAMETRY_I_RAPORTY.md](../PARAMETRY_I_RAPORTY.md). Nowe artefakty mają format 3.
Kontrola odrzuca brak pokrycia walidacji/testu oddziału i wykrywa luki pomiędzy
pomiarami MDD w tej samej dobie, bez rozszerzania oczekiwanej obecności poza
pierwszy/ostatni pomiar. Częściowa pogoda ma osobny status i kod 2.
Nie wykonano treningu na rzeczywistych danych PGE ani połączenia z firmowym SQL.

Zmiany 15 października 2026: użytkownik zgłosił błąd UTF-8 przy bajcie `0xa3`
po edycji mapowania na Windows i brak kodu `ZŁT`. Potwierdził `ZŁT = Łódź teren`
oraz `ŁZE = Łódź miasto`, oba z pogodą Łodzi. Dodano alias `ZŁT/ZLT → ZEUD`.
Odczyt mapowania używa
wspólnej obsługi CSV (UTF-8, Windows-1250, UTF-16 z BOM; przecinek/średnik/tabulator),
waliduje statusy, nagłówki i aktywne wpisy, pokazuje pełną ścieżkę pliku.
`SCIEZKA_MAPOWANIA` w launcherze treningu pozwala wybrać własny CSV.
Algorytm prognozy i kryteria doboru modeli pozostają te same.

Uzupełnienie 15 października 2026: po komunikacie o energii biernej użytkownik
potwierdził, że kierunek ma wynikać wyłącznie z B: `1` czynne pobranie,
`-1` czynne oddanie. Zastąpiono wcześniejsze pierwszeństwo E i fallback B.
Treść E pozostaje opisem i nie blokuje importu. Inne/brakujące kody B są błędem
z przykładami rekordów; oryginalny kod zachowuje `kierunek_code_zrodlowy`.
Korekty i duplikaty nadal rozróżnia klucz oddział/MDD/kierunek B/godzina UTC.
Sprzecznych wartości najlepszej korekty nie sumujemy; błąd pokazuje rekordy.
Po zmianie znaczenia kierunku należy ponowić trening. Nie zmieniano algorytmów
uczenia ani parametrów CatBoost.

Kolejne zgłoszenie 15 października 2026 dotyczyło niepoprawnej daty/godziny.
Użytkownik potwierdził liczbowy zapis H od 1 do 25; nie przekazał jeszcze
przykładów F. Bez tych danych nie zmieniano interpretacji dat ani konwencji DST.
W próbie syntetycznej domyślny `pd.to_datetime` błędnie odczytał `01.07.2024`
jako 7 stycznia i odrzucił `15.07.2024`; mieszane ISO z czasem/bez także dały NaT.
Trzeba porównać te przypadki z rzeczywistymi F przed wyborem reguły parsera.
Dodano oryginalne F/H do diagnostyki i raport `bledne_daty_godziny.csv`
w folderze artefaktów przy uruchomieniu przez launcher. Powody odróżniają brak
daty, niecałkowite/niepoprawne H i nieistniejące godziny zmiany czasu.
Naprawiono odczyt H, który wcześniej wycinał pierwszą liczbę z dowolnego tekstu.
`SCIEZKA_POGODY` rozpoznaje teraz `.sql` jako zapytanie (na zdjęciu użytkownik
wpisał tę ścieżkę w miejsce pliku eksportu). Puste ustawienie nadal wybiera
domyślny SQL, a CSV/XLSX pozostają eksportami pogody.

Następne zdjęcie wskazało 1130 rekordów z komunikatem o nieistniejącej godzinie
wiosną; przykłady mają prawidłową dobę `2024-03-31` i H=3. To nie potwierdza
problemu parsera F: do wyjaśnienia pozostaje numeracja godzin energii w dni DST.
Zapytano użytkownika, czy są to kolejno 1–23/25, czy wcześniejszy wariant
z dodatkową 02:00 oznaczoną jako 25. Nie zmieniono tej konwencji bez odpowiedzi.
Naprawiono niezależny błąd zakresu: `start_date` jest stosowane przed walidacją
i agregacją, a nie po nich. Rozpoznane starsze doby są zliczane jako
`przed_data_startowa`; nierozpoznane daty nadal wywołują diagnostykę.

Następnie użytkownik wycofał pewność co do numeracji kolejnych rzeczywistych
godzin: „znaczy nie wiem wsm, zrób diagnostykę tego to się dowiemy”.
Nie traktuj wcześniejszego „tak” jako potwierdzonego kontraktu DST.
Dodano [diagnostykę godzin](../DIAGNOSTYKA_GODZIN.md) i skrypt
`uruchom_diagnostyke_godzin.py`, przejmujący ścieżki z lokalnego launchera
treningu. Audyt bada cały eksport bez `start_date`, SQL, wyboru korekt i uczenia.
Porównuje wiosenne wzorce 1–23 oraz 1,2,4…24 osobno dla doby/oddziału/MDD/B/G.
Jesienne 1–25 nie rozstrzyga znaczenia H25; potrzebny może być opis eksportu
lub niezależny znacznik UTC. Zera nie są automatycznie technicznymi wypełnieniami.
Skrypt zapisuje podsumowanie bez nazw MDD oraz lokalne szczegółowe CSV.
Przeliczanie czasu treningu, predykcji i pogody pozostaje bez zmian do analizy
rzeczywistego raportu. W chmurze nadal nie ma rzeczywistego pliku energii PGE.

Aktualna decyzja użytkownika, 15 października 2026: na razie wyłączyć całe
doby zmiany czasu, zarówno krótsze, jak i dłuższe, i zachować modelowanie.
W kolejnych zdjęciach potwierdzono H25 na 2023-10-29, 2024-10-27, 2025-10-26
oraz niezerowe rekordy H25: odpowiednio 88, 110 i 138. Wiosną 2024-03-31 jest
zestaw 1,3…24, bez H2, z niezerowymi wartościami H3. Definicja H pozostaje
niepotwierdzona; nie należy arbitralnie zmieniać numeracji ani usuwać samych H25.

CLI treningu i strzałka VS Code domyślnie wyłączają całe doby inne niż 24 h
w `Europe/Warsaw`, po start_date i przed walidacją/agregacją energii.
`include_dst_days=False` w launcherze jest ustawieniem domyślnym;
`--include-dst-days` przywraca poprzednie zachowanie. Niskopoziomowy loader
ma opcjonalny `exclude_dst_days=False` dla zgodności; CLI przekazuje `True`.
`pominiete_dni_zmiany_czasu.csv` i arkusz `Pominiete_doby` pokazują daty,
długość doby i liczbę wyłączonych rekordów. Kontrola portfela nie zalicza
celowo wyłączonych dni do przerw MDD. Model zapisuje
`input_schema.exclude_dst_days`; audyt nowych eksportów stosuje tę samą regułę,
a starsze modele bez pola zachowują wcześniejszą obsługę. Prognoza nadal liczy
72 rzeczywiste godziny. Wykluczone cele nie biorą udziału w treningu, walidacji
ani teście; jakość w tych dobach nie jest oceniona. Nie zmieniono algorytmów,
parametrów CatBoost, mapowania zwykłych godzin ani pogody i cutoffu origin.
