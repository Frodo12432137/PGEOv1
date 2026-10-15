# Sprawdzenie numeracji godzin w eksporcie energii

Na zdjęciu błędu są rekordy `2024-03-31`, H=3. Obecne przeliczanie zakłada,
że H3 oznacza lokalną 02:00, która tego dnia nie istniała. Eksport może jednak
numerować kolejne rzeczywiste godziny: wtedy H3 oznaczałoby 03:00.
Użytkownik nie potwierdził ostatecznie konwencji. Diagnostyka porównuje obie
możliwości, zanim zmienimy przeliczanie i łączenie energii z pogodą.

## Uruchomienie strzałką w VS Code

1. Pobierz aktualny kod, zachowując swoje ustawienia
   `uruchom_trening_bilansow.py`.
2. Otwórz **`uruchom_diagnostyke_godzin.py`** i kliknij ▶ **Run Python File**.
   Puste `SCIEZKA_ENERGII` pobiera ścieżkę z pliku treningowego; jeśli tam też
   jest pusta, otwiera wybór CSV/XLSX/XLSM.
3. Otwórz **`FOLDER_MODELU/diagnostyka_godzin/podsumowanie_godzin.txt`**.
   Jeśli folder modelu to `Wynik`, raport będzie w
   `Wynik/diagnostyka_godzin/podsumowanie_godzin.txt`.
4. Przekaż treść podsumowania do analizy. Szczegółowe CSV pozostają lokalnie;
   zawierają nazwy MDD i wartości pomiarów.

Ścieżkę i folder można też wpisać bezpośrednio w nowym skrypcie.
Ścieżki względne w tym skrypcie liczone są od folderu projektu.
Program korzysta z bibliotek projektu, bez instalowania nowych zależności.

Odpowiednik w terminalu, uruchomiony z katalogu projektu:

```powershell
python -m mdd_forecasting.energy_time_diagnostics --energy "C:\Dane\dane_po_transpozycji.csv" --output-dir "Wynik\diagnostyka_godzin"
```

Badany jest **cały plik**, także dni sprzed `start_date` treningu. Dzięki temu
wiosenna zmiana czasu z marca 2024 jest widoczna mimo startu treningu w lipcu.
Odczyt używa tych samych arkuszy i parsera F/H co import treningowy.
Diagnostyka nie wymaga poprawnego mapowania oddziałów ani dostępu do SQL.
Nie uruchamia treningu, nie zapisuje modeli, nie wybiera korekt i nie zmienia
pliku źródłowego ani przyjętej w modelu numeracji godzin.

## Co rozróżnia raport

| Doba | Kolejne rzeczywiste godziny | Numeracja zegarowa z dodatkową H25 |
|---|---|---|
| zwykła | 1–24 | 1–24 |
| wiosenna, 23 h | 1–23, H3 = 03:00 | 1, 2, 4…24; nie ma H3 |
| jesienna, 25 h | 1–25; H3 i H4 = dwie 02:00, H25 = 23:00 | 1–25; H3 i H25 = dwie 02:00, H4 = 03:00 |

Pełny wiosenny zestaw pozwala wskazać typowy wariant etykiet. **Samo jesienne
1–25 nie rozstrzyga znaczenia H25.** Nie rozstrzyga go również kolejność
wierszy w pliku ani sama wartość energii. Jeśli raport nie daje dostatecznej
odpowiedzi, potrzebny jest opis eksportu lub niezależny czas UTC.

Zestawy godzin są liczone osobno dla doby, oddziału, MDD, B i wersji korekty G.
Godziny dwóch niepełnych MDD lub różnych korekt nie tworzą pozornej pełnej serii.
`PELNY_WZORZEC` oznacza komplet etykiet, a nie pewność poprawności pomiarów.
Podsumowanie osobno liczy zera, braki/niepoprawne I oraz niezerowe skończone I
w godzinach rozróżniających warianty. Zero nadal jest obserwacją; nie traktujemy
go automatycznie jako technicznego wypełnienia nieistniejącej godziny.

Wstępny wynik o pełnym wzorcu pomija serie z niepoprawnymi kluczami,
problemami I i konfliktami wartości tej samej korekty. Wszystkie te rekordy
pozostają w szczegółach. Raport nie daje automatycznej zgody na przesunięcie
energii względem pogody. Nie stosuje wykrytego wzorca do modelu.

## Pliki wynikowe

| Plik | Zawartość |
|---|---|
| `podsumowanie_godzin.txt` | zakres, liczniki wejścia, wzorce w dniach DST, wstępny wynik i jego ograniczenia; bez nazw MDD i ścieżki źródłowej |
| `rekordy_zmiany_czasu.csv` | wszystkie źródłowe rekordy z dni zmiany czasu, arkusze i numery wierszy, surowe F/H oraz odczytane wartości |
| `wzorce_serii.csv` | zestawy godzin pojedynczych serii i wersji G, powtórzenia widoczne jako `rekordy > unikalne_H`, konflikty wartości tej samej G i zgodność obu wzorców |
| `liczniki_godzin.csv` | liczba rekordów, zer, problemów I i niezerowych skończonych I dla każdej etykiety H w dniu DST |
| `porownanie_utc.csv` | obie hipotetyczne interpretacje H1–H25 w dniach DST; lokalny czas zawiera offset, aby odróżnić powtórzoną 02:00 |
| `problemy_wejscia.csv` | oryginalne rekordy z nierozpoznaną F, H poza całkowitym 1–25, B innym niż ±1, G poza 0–4 oraz brakującą/niepoprawną I |

CSV używają separatora `;`, przecinka dziesiętnego i UTF-8 BOM. Nagłówki
`doba_handlowa` i `godzina_handlowa` w szczegółach zachowują wartości źródłowe;
`doba_odczytana` i `godzina_odczytana` pokazują wynik parsera.
Kontrola I w tym audycie sygnalizuje także brak wartości; nie zastępuje walidacji
i reguł agregacji treningowej. Dane nie są usuwane z powodu żadnego z tych flag.
