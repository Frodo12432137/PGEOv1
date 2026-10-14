# Trening strzałką w VS Code: pliki, modele i statystyki

Otwórz cały folder projektu w VS Code. Wybierz interpreter Pythona, w którym
zainstalujesz biblioteki, a potem otwórz `uruchom_trening_bilansow.py`.
Na początku tego pliku są wszystkie ustawienia użytkownika.

```python
SCIEZKA_ENERGII = r"C:\Dane\klienci.xlsx"
FOLDER_MODELU = r"C:\Dane\model_bilansu"
SCIEZKA_POGODY = r""  # puste: firmowy SQL
```

Wklej ścieżkę do rzeczywistego Excela z historią pomiarów klientów w układzie
A:I. Sam spis klientów bez godzinowych pomiarów nie wystarczy do treningu.
Puste `SCIEZKA_ENERGII` otwiera okno wyboru pliku. Wypełniona ścieżka pozwala
działać bez okien. Ścieżki względne są liczone od folderu projektu, niezależnie
od katalogu roboczego terminala VS Code. Prefiks `r` pozwala wkleić ścieżkę Windows.

Pusta ścieżka pogody wybiera firmowy SQL; wymaga sieci/VPN, uprawnień Windows
i ODBC Driver 17. Do pracy z eksportem ustaw np.
`SCIEZKA_POGODY = r"C:\Dane\pogoda_historia.csv"`. To musi być archiwum wydań,
z czasem celu oraz czasem dostępności każdej prognozy. Samo ostatnie wydanie
nie wystarczy do historycznej oceny 72 h.

Zainstaluj podstawowe biblioteki w terminalu VS Code:

```powershell
python -m pip install -r requirements.txt
```

Aby porównywać także CatBoost:

```powershell
python -m pip install -r mdd_forecasting/requirements-production.txt
```

Zapisz ustawienia i kliknij **▶ Run Python File / Uruchom plik Python** w prawym
górnym rogu. W terminalu zobaczysz porównywane modele i końcowe błędy.
Raporty i modele znajdziesz w `FOLDER_MODELU`. Nowy trening w tym samym folderze
zastępuje jego pliki; do zachowania poprzedniego eksperymentu wybierz inny folder.

## Dobór modelu i pogody

Program uczy oddzielnie pobrania i oddania. Oba modele korzystają ze wspólnej
historii oddziałów, ale wyniki każdego OSD pozostają osobne. Bilans jest różnicą.

`models="auto"` porównuje HistGradientBoosting oraz CatBoost, jeśli jest
zainstalowany. Bez CatBoost program jawnie informuje o pominięciu tej rodziny.
`models="hist_gradient_boosting"` albo `models="catboost"` wymusza jedną rodzinę;
wymuszony CatBoost bez biblioteki kończy się komunikatem instalacyjnym.

Warianty pogody:

| Wariant | Zmienne dodawane do kalendarza i kodu oddziału |
|---|---|
| `kalendarz` | bez pogody |
| `temperatura` | temperatura, stopniogodziny grzania/chłodzenia |
| `slonce` | promieniowanie całkowite i bezpośrednie |
| `temperatura_slonce` | obie grupy powyżej |
| `pogoda_podstawowa` | temperatura, słońce, chmury i wiatr |
| `pogoda_pelna` | dodatkowo opad, śnieg/albedo i widoczność |

Z listy `weather_variants` można usunąć warianty, których nie chcesz porównywać.
Całkowicie puste cechy są wykluczane na podstawie początkowego treningu.
Warianty dające identyczny zestaw cech nie są liczone ponownie.

Wybór rodziny, pogody i parametrów następuje na chronologicznej walidacji,
osobno dla pobrania i oddania. Kryterium to średni MAE znormalizowany skalą
każdego oddziału wyznaczoną na treningu. Dzięki temu wybór uwzględnia również
mniejsze oddziały. Późniejszy test nie uczestniczy w wyborze.

Przy `tune=True` program porównuje trzy konfiguracje każdej rodziny modelu:
podstawową oraz dwa warianty złożoności/regularyzacji i kroku. Z dwiema rodzinami
i sześcioma zestawami pogody daje to do 72 prób dla obu kierunków, a następnie
dopasowania do oceny testowej i modele końcowe. To może potrwać na dużej historii.
Na pierwsze sprawdzenie plików ustaw `tune=False` i np. `max_iter=80`.
Do właściwego porównania wróć do większej liczby iteracji i wydziel osobny test.

Nie da się wskazać najlepszego modelu dla PGE bez pomiarów i archiwum pogody.
CatBoost jest dodatkowym kandydatem, a nie automatyczną gwarancją poprawy.
Raport pokazuje też, gdzie wybrany ML przegrywa ze zwykłym profilem historycznym.

## Parametry w słowniku PARAMETRY

| Parametr | Znaczenie / kiedy zmieniać |
|---|---|
| `models` | `auto`, `hist_gradient_boosting` lub `catboost` |
| `tune` | `True`: trzy konfiguracje rodziny; `False`: jedna konfiguracja podana w kodzie |
| `max_iter` | liczba iteracji obu rodzin; więcej to dłuższe uczenie i możliwe przeuczenie |
| `learning_rate` | krok w zakresie (0, 1]; mniejszy zwykle wymaga większej liczby iteracji |
| `max_leaf_nodes` | liczba liści HistGradientBoosting; większa pozwala odtwarzać bardziej złożone zależności |
| `min_samples_leaf` | minimum obserwacji w liściu HistGradientBoosting; większe wygładza wynik |
| `l2_regularization` | regularyzacja HistGradientBoosting, nieujemna |
| `catboost_depth` | głębokość CatBoost, 1–12; 6 jest punktem startowym |
| `catboost_l2_leaf_reg` | nieujemna regularyzacja CatBoost |
| `threads` | liczba wątków CPU; np. 4 pozwala ograniczyć obciążenie laptopa |
| `random_state` | ziarno losowości zapisane w manifeście; powtarzalność w tym samym środowisku |
| `weather_variants` | jawna lista zestawów pogody do porównania |
| `importance_rows` | maksymalna próbka testu do grupowej ważności pogody; 0 wyłącza |
| `start_date` | pierwsza doba historii energii; użyta będzie część wspólna z pogodą |
| `validation_days` | długość okna wyboru wariantu i parametrów |
| `test_days` | późniejsze okno niezależnej oceny; nie stroić według jego wyników |
| `label_delay_hours` | embargo między targetami treningu a oceną; nie zastępuje czasu publikacji korekt |
| `horizon_hours` | horyzont treningu, domyślnie 72; obsługiwane 1–168 godzin |
| `origin_step_hours` | odstęp symulowanych uruchomień w historii, domyślnie 12 godzin |
| `min_train_rows` | minimum zleceń treningowych całego kierunku |
| `max_train_rows` | limit najnowszych zleceń treningowych kierunku; ta sama godzina może mieć kilka horyzontów |
| `min_evaluation_targets` | minimum unikalnych godzin każdego oddziału/kierunku w walidacji i teście; domyślnie 24 |
| `portfolio_grace_days` | próg dłuższego braku MDD; nie oznacza końca umowy |

`tune=True` używa podanych ustawień jako punktu wyjścia i porównuje również ich
warianty. Faktyczne parametry wybranego modelu znajdziesz w Excelu i manifeście.
Za krótka historia albo brak oceny choć jednego oddziału przerywa trening.
Nie zmniejszaj minimalnych wymagań tylko po to, aby ukryć niepełny eksport.

## Od czego zacząć czytanie wyników

Otwórz **`raport_treningu.xlsx`**. Arkusz `Czytaj_najpierw` wyjaśnia metryki
i ograniczenia, `Ustawienia` zapisuje konfigurację przebiegu, a `Wybrane_modele`
pokazuje rzeczywiście wybraną rodzinę, pogodę, parametry oraz kolumny cech.

| Arkusz | Na jakie pytanie odpowiada |
|---|---|
| `Podsumowanie` | który oddział/kierunek ma największe MAE, bias i czy ML pokonał profil? |
| `Horyzonty_24h` | jak błąd zmienia się w 1–24, 25–48, 49–72 godzinach? |
| `Horyzont_1_do_72` | jaki jest błąd każdej dokładnej odległości od uruchomienia? |
| `Godziny`, `Dni_tygodnia`, `Miesiace`, `Doby` | kiedy model myli się najczęściej i najbardziej? |
| `Przebiegi` | czy konkretne historyczne uruchomienie było szczególnie słabe? |
| `Najwieksze_pomylki` | po 20 największych błędów każdego oddziału/kierunku, z originem, celem i pogodą |
| `Warunki_pogody` | jak wyglądają błędy w przedziałach temperatury, promieniowania i wiatru? |
| `Porownanie_walidacja` | co porównano i dlaczego wybrano daną rodzinę/pogodę/parametry? |
| `Waznosc_pogody` | jak zaburzenie grup pogody wpływa na błąd wybranego modelu na teście? |
| `Pokrycie_okien`, `Pokrycie_pogody` | ile danych rzeczywiście było do oceny i ile historii miało pogodę? |
| `Kontrola_energii`, `Kontrola_modelu`, `Portfel` | czy są luki, korekty, zmiany MDD lub założenia wymagające sprawdzenia? |
| `Wykresy`, `Wykres_godzinowy` | wizualne porównanie MAE z profilem i błędów w ciągu doby |

Pełne odpowiedniki tabel są w CSV (separator `;`, przecinek dziesiętny, UTF-8 BOM).
Excel ogranicza każdą tabelę do 20 000 wierszy; CSV zachowuje całość.
Źródłowa historia oddziałów pozostaje w `historia_oddzialow.csv`.

MAE, RMSE i bias są w jednostkach źródłowych energii. P90/P95 opisują rozkład
błędów testu, a nie przyszły przedział ufności. WAPE przy małym bilansie może
być bardzo duży; oceniaj go razem z MAE i biasem. sMAPE obsługuje zera, a R²
może być ujemne. Porównanie ML/profil wykorzystuje wyłącznie ich wspólne wiersze.

`n` liczy zlecenia origin/cel, natomiast `n_godzin` liczy unikalne godziny UTC.
Ocena opiera się na części wspólnej realizacji i pogody; osobny raport pokrycia
pokazuje również pominięte zlecenia. Krótki test nie potwierdza jakości we
wszystkich sezonach. Do wdrożenia oceniaj również kilka rozłącznych okresów.

Ważność pogody nie dowodzi przyczynowości. Nieużyta grupa nie otrzymuje
fikcyjnej ważności 0, tylko oznaczenie `uzyta_w_modelu=False`. Ważność ujemna
może wynikać z szumu. Nie dobieraj nowych cech na podstawie tego samego testu
bez wydzielenia kolejnego, niezależnego okresu oceny.

## Prognoza po treningu

Otwórz `uruchom_prognoze_bilansow.py`, wskaż ten sam `FOLDER_MODELU` i pogodę
(puste = SQL), a następnie kliknij ▶. Pusty `FOLDER_PROGNOZY` tworzy osobny
podfolder z datą i czasem. Puste `CHWILA_PROGNOZY` oznacza bieżącą chwilę.
Opcjonalny `SCIEZKA_ENERGII` służy wyłącznie kontroli zmian klientów.

Przy ośmiu oddziałach i 72 godzinach otrzymasz 576 wierszy bilansu.
`PROGNOZA_NIEPELNA_POGODA` oznacza, że model zwrócił liczbę mimo częściowego
braku wybranych cech. Lista braków jest w `szczegoly_prognozy.csv`.
Brak całej wymaganej pogody pozostawia pustą prognozę. Obie sytuacje zwracają
kod zakończenia 2, aby dało się je zauważyć także przy automatycznym uruchamianiu.

Nowy format artefaktów to 3. Odczyt zachowuje wsparcie dla formatów 1/2.
Wersja scikit-learn, a przy wybranym CatBoost również jego wersja, musi być
zgodna z treningiem. Aby korzystać z nowego porównywania modeli, wykonaj trening.

## Kontrole danych i granice oceny

Program odrzuca nazwy energii biernej zawierające rdzeń `biern` i wartości
nieskończone. Luka między pomiarami tego samego MDD w tej samej dobie powoduje
brak sumy oddziału w tej godzinie. Nie zakładamy obecności przed pierwszym
ani po ostatnim pomiarze MDD; do takich ocen potrzebne są dodatkowe informacje
o kompletności eksportu i portfelu. Przerw nie uzupełniamy zerami.

Nie znamy hierarchii MDD sumarycznych/składowych ani czasu publikacji korekt.
Lagi energii pozostają wyłączone, a ocena jest retrospektywna. Przed właściwym
treningiem potwierdź jednostki, czas, numerację handlową i mapowanie oddziałów.
Testy syntetyczne sprawdzają działanie programu, nie dokładność na portfelu PGE.
