/* Archiwum wydań dla treningu oraz aktualna pogoda dla predykcji 72 h.
   Python uruchamia ten plik przez pyodbc z uwierzytelnianiem Windows.
   Nie filtrujemy czyOstatni/czyOstatniaPrognoza2: te flagi usunęłyby archiwum.
   O wyborze wydania decyduje origin każdego zlecenia w Pythonie, zawsze po UTC.
   ? = od CET, do CET wyłącznie, maksymalny czas źródła UTC, właściciel, typ.
   NULL dla właściciela/typu oznacza brak filtra. Zdjęcie pokazuje typ
   'klient zagregowany'; nie zakładamy, że jest to 'Open Meteo'.
   czasDanychZrodlaUTC traktujemy jako czas dostępności według opisu użytkownika.
   Jeśli istnieje późniejszy czas ingestii, trzeba zastąpić go tym czasem.
*/
SET NOCOUNT ON;
DECLARE @od datetime2 = ?;
DECLARE @do datetime2 = ?;
DECLARE @asofUTC datetime2 = ?;
DECLARE @owner nvarchar(100) = ?;
DECLARE @typ nvarchar(100) = ?;
SELECT [execId], [punkt], [dataCET], [godzinaHandlowa25],
       [dataGodzinaCET], [dataGodzinaUTC],
       [czasDanychZrodlaCET], [czasDanychZrodlaUTC],
       [temperatura], [predkoscWiatru], [kierunekWiatru], [zachmurzenie],
       [intensywnoscOpadowKonwekcyjnych], [widocznosc],
       [calkowitePromieniowanieSloneczneGodzinowe],
       [bezposredniePromieniowanieSloneczneGodzinowe],
       [albedoPrognozowane], [warstwaSniegu]
FROM [PGESA_MarketAnalytics].[wa].[vPogodaPrognoza]
WHERE [punkt] IN (N'Białystok', N'Lublin', N'Rzeszów', N'Łódź',
                  N'Skarżysko-Kamienna', N'Warszawa', N'Zamość')
  AND [dataGodzinaCET] >= @od AND [dataGodzinaCET] < @do
  AND [czasDanychZrodlaUTC] <= @asofUTC
  AND [pelnaGodzina] = 1
  AND (@owner IS NULL OR [wlasciciel] = @owner)
  AND (@typ IS NULL OR [typ] = @typ)
ORDER BY [czasDanychZrodlaUTC], [punkt], [dataGodzinaUTC];
