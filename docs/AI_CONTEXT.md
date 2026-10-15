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
| B | kierunek MDM: `1` pobranie, `-1` oddanie; kontrola/fallback dla E |
| C | grupa OSD MDM; informacja źródłowa, nie klucz ośmiu oddziałów |
| D | nazwa MDD; identyfikator serii, czasem agregatu klientów |
| E | kierunek MDD; energia czynna pobrana lub oddana |
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
