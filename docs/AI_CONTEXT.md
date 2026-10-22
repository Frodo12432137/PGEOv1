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

Implementacja porównuje HistGradientBoosting oraz dostępne CatBoost, LightGBM i XGBoost,
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
Zachowano oryginalne F/H w podstawowej walidacji danych. Powody odróżniają brak
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

Użytkownik wycofał pewność co do numeracji kolejnych rzeczywistych godzin.
Przeprowadzony wtedy osobny audyt został później usunięty na jego prośbę.
Nie traktuj wcześniejszego „tak” jako potwierdzonego kontraktu DST.

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

Kolejna decyzja 15 października 2026: usunąć osobną diagnostykę godzin i dodać
backtest tego samego modelu do pokazania wyników na spotkaniu. Wyłączenie całych
dób DST pozostaje. Usunięto skrypt i moduł audytu oraz dodatkowy eksport
niepoprawnych godzin; podstawowe walidacje i log błędu pozostają.

`uruchom_backtest_bilansow.py` przejmuje ścieżki, SQL i pełne parametry
`uruchom_trening_bilansow.py`. Cztery lokalne daty wybierają wcześniejszą
historię treningową i późniejsze uruchomienia prognozy. Model trenuje raz
przez produkcyjne train_branch_models(), a potem używa predict_branches()
bez dalszego uczenia. Każdy origin przewiduje pełny horyzont, domyślnie 72 h.
Dane zewnętrznego testu nie wpływają na fit(), profil, strojenie ani portfel
treningowy. Obowiązuje embargo od końca doby TRENING_DO.

Wyniki powstają lokalnie w osobnym podfolderze backtest_bilansu: raport HTML
bez Internetu, Excel z wykresami i błędami, pełne CSV i status. Wewnętrzna
ocena i ważność pogody są w trening/, odrębnie od zewnętrznej oceny. Braki
realizacji nie są zerami; cele DST zostają w prognozach, ale poza oceną.
Aktualne ustawienie to brak limitu czasu (`max_runtime_hours=None`), z cache
gotowych prób i modelu. Opcjonalny limit pozostaje dostępny; status LIMIT_CZASU
/ kod 124 oznacza wtedy niekompletny wynik.
Wznowienie SQL używa tego samego snapshotu pogody; resume=False pobiera nowy.
Źródła, ustawienia, kod i wersje bibliotek identyfikują cache pełnymi skrótami.
Instrukcja: [BACKTEST.md](../BACKTEST.md). Brak danych PGE/SQL w chmurze;
syntetyczne wyniki testów programu nie stanowią oceny dokładności biznesowej.

Dalsza prośba użytkownika 15 października 2026: usunąć domyślny limit, dodać
błędy każdej godziny i każdego OSD, miesiące, jakość pogody, rekomendacje
parametrów i rozbudować porównanie ML. Launcher nadal dziedziczy parametry
produkcji, więc nowe rodziny i zespół są wspólne dla obu przepływów.

Auto porównuje HistGradientBoosting, CatBoost, LightGBM i XGBoost, jeżeli są
zainstalowane. requirements-production.txt obejmuje wszystkie; Linux x86_64
używa xgboost-cpu, pozostałe platformy xgboost, z metodą hist na CPU. Domyślny
launcher włącza ensemble: dwa najlepsze modele (preferowana inna rodzina
drugiego) są porównywane w wagach 25/75, 50/50, 75/25 wyłącznie na walidacji.
Zespół zostaje tylko po poprawie wyniku; modele są refitowane z prawidłowym
odcięciem przed wewnętrznym testem i potem na pełnej historii treningowej.
Do kalendarza dodano godzinę tygodnia, dzień roku oraz dzień przed/po święcie.
Brak lagów energii pozostaje. Format modelu 4, odczyt 1–4; wersje wszystkich
członków zespołu są zapisane i sprawdzane. Nowy wybór wymaga retreningu.

Raport rozdziela lata/miesiące (rok_miesiac), dodaje błąd każdej pary
origin/cel/OSD/kierunek, zbiorcze statystyki błędów, wiek pogody i mapę godzin
OSD w HTML. Rekomendacje modelu/pogody pochodzą tylko z wcześniejszej
walidacji i opisują już zastosowane ustawienia; wskazują też porażki z profilem.
Kontrola pogody mierzy kompletność, wiek wydań i podejrzane zakresy, nie
meteorologiczną trafność: brak obserwacji meteo. Nie nazywaj tych kontroli
błędem prognozy temperatury ani dowodem najlepszego modelu na rynku.
Testy rzeczywistych bibliotek i zespołu: tests.test_model_selection.

Kolejna decyzja użytkownika 15 października 2026, po błędzie ujemnego I:
pomijać ujemne i odstające pomiary. Filtry są domyślnie włączone w CLI,
launcherze i backteście, także gdy stary lokalny PARAMETRY nie ma nowych pól.
B=-1 i ujemny bilans pozostają poprawne; filtr dotyczy ilości I.

Po wyborze korekty zapisujemy oryginalną wartość i zastępujemy wykluczony
składnik brakiem. Nie wybieramy starszej wersji i nie sumujemy tylko reszty
MDD w OSD. Statystyczny filtr górnych ekstremów (nie procentowe odcięcie)
używa max(Q3+6*IQR, 3*Q3) dodatniej referencji własnego MDD/kierunku/OSD,
z hierarchią miesiąc+godzina, miesiąc, godzina, cała seria i minimum 30 pomiarów.
Zera i małe dodatnie wartości pozostają, a nowe MDD bez referencji nie są
filtrowane zgadywanym progiem innego klienta. Każde wykluczenie ma raport i próg.

Referencja kończy się przed wewnętrzną walidacją z embargo. Granicę podziału
wyznaczamy z dostępnej pogody przed filtrem i przekazujemy do core jako
split_cutoff, żeby odrzucenie końca historii nie przesuwało walidacji do
referencji filtra. Stan energy_filter jest zapisany w modelu; audyt i backtest
stosują go bez refitu. Forecasty nie są usuwane; wykluczony target ma status
POMIAR_WYKLUCZONY_FILTREM i obniża pokrycie. Miary są warunkowe po filtrze;
statystyczny ekstremum może być rzeczywistym szczytem, więc zachowaj raport
oryginałów i możliwość wyłączenia filtra. Nieskończone I, błędy B/czasu i
sprzeczne najlepsze korekty nadal wymagają wyjaśnienia. Niskopoziomowy loader
bez opcji pozostaje ścisły dla zgodności, CLI przekazuje nową politykę jawnie.
Testy: tests.test_energy_filter. Źródłowy eksport nie jest zmieniany.

Decyzja użytkownika 16 października 2026 po błędzie zgodności godziny
handlowej pogody: energia i pogoda mają być łączone i przetwarzane po UTC.
Było to już zasadą kluczy modelu; blokowała dodatkowa walidacja H25/CET.
W bilansach poprawne źródłowe `dataGodzinaUTC` oraz `czasDanychZrodlaUTC`
mają teraz pierwszeństwo. Rozbieżne CET/dataCET/godzinaHandlowa25 są
metadanymi do audytu, bez przestawiania UTC i bez blokowania poprawnego UTC.
Nie potwierdza to fizycznej poprawności źródłowych UTC; brak danych źródłowych
PGE i połączenia z firmowym SQL w chmurze nadal ogranicza weryfikację.

`weather_time.py` dopuszcza brak UTC tylko przy jednoznacznej konwersji czasu
lokalnego z Europe/Warsaw albo z jawnym offsetem. Nie zgaduje godziny podczas
zmiany czasu. Błędne niepuste UTC, brak punktu/czasu i niepełna godzina celu
zatrzymują odczyt z oryginałami w `bledy_czasu_pogody.csv`. Nie znikają po cichu.
Późne wydania pozostają w audycie, a as-of i dodatni horyzont wykluczają
je z wcześniejszych prognoz. Sprzeczne cechy tego samego klucza UTC nadal
wymagają filtra właściciela/typu. Oryginalne duplikaty pozostają do audytu;
identyczne wersje są deduplikowane dopiero przy łączeniu.

Raporty `kontrola_czasu_pogody.csv` i `rozbieznosci_czasu_pogody.csv` są
w treningu, prognozie i backteście, także po wznowieniu z cache; Excel ma
odpowiednie arkusze. To kontrola czasu wejściowej pogody, nie przywrócenie
usuniętej osobnej diagnostyki numeracji godzin energii. Numer źródła w raporcie
to kolejność rekordu wyniku SQL/pliku, nie numer arkusza Excela.

SQL domyślny filtruje `dataGodzinaUTC` i ma kontrakt `-- PGEO_VALID_RANGE: UTC`;
aplikacja przekazuje świadome strefy granice UTC. Własny stary SQL bez znacznika
otrzymuje nadal granice CET, co adapter jawnie komunikuje. Nie nadpisuj lokalnych
zapytań, ścieżek i dat użytkownika przy dostarczaniu poprawki. Energia A:I
pozostaje odczytywana jako czas lokalny i przeliczana raz na UTC. Zgoda na
wyłączenie całych dób DST pozostaje; UTC nie rozstrzyga nieznanej numeracji H25
energii. Daty wejściowe, lokalny kalendarz i podziały raportu nadal mają jawną
semantykę Europe/Warsaw. Nie zmieniono cech modelu, parametrów ani formatu modelu.
Testy: `tests.test_weather_utc` oraz istniejące testy obu przepływów, treningu,
backtestu i wznowienia. Dane syntetyczne nie dowodzą dokładności modelu PGE.


Zmiana 22 października 2026: konflikty pogody są oznaczane przy imporcie,
ale blokują dopiero użycie sprzecznego wydania wybranego przez as-of.
Nieużywane wydania nie zatrzymują całego archiwum. Raport
`konflikty_wersji_pogody.csv` zapisuje wszystkie konfliktujące rekordy przed
treningiem, także przy wznowieniu backtestu. Domyślny SQL zwraca teraz również
właściciela i typ. Nie rozstrzygamy remisu przez execId ani starsze wydanie.
Sprawdzono syntetyczne przypadki, bez dostępu do danych/SQL użytkownika;
nie potwierdzono, czy rzeczywisty konflikt dotyczy używanego wydania.
