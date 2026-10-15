"""Uzupełnij ustawienia poniżej i kliknij ▶ Run Python File w VS Code.

Ścieżki względne liczymy od folderu projektu. Opis: PARAMETRY_I_RAPORTY.md.
"""
from mdd_forecasting.branch_launcher import configured_launch


# ================== TWOJE PLIKI ==================
SCIEZKA_ENERGII = r""  # Wklej np. r"C:\Dane\klienci.xlsx". Puste = okno wyboru.
FOLDER_MODELU = r"model_bilansu"  # Tu powstaną modele, Excel i wszystkie CSV.
SCIEZKA_POGODY = r""  # Puste = domyślny SQL. Można też podać własny .sql lub archiwum CSV/XLSX.
SCIEZKA_MAPOWANIA = r""  # Puste = mdd_forecasting/config/oddzial_miasto.csv.

# ================== MODEL I DOKŁADNOŚĆ ==================
PARAMETRY = {
    "models": "auto",  # HistGradientBoosting + opcjonalny CatBoost; można wskazać jedną rodzinę.
    "tune": True,  # Trzy ustawienia każdej rodziny × warianty pogody. False = szybciej.
    "max_iter": 300,  # Liczba iteracji/drzew. Więcej zwiększa czas; nie gwarantuje poprawy.
    "learning_rate": 0.06,  # Krok uczenia. Niższy zwykle wymaga więcej iteracji.
    "max_leaf_nodes": 31,  # Złożoność HistGradientBoosting; tune porównuje też inne wartości.
    "min_samples_leaf": 30,  # Więcej = większe wygładzanie i mniejsza podatność na szum.
    "l2_regularization": 2.0,  # Regularyzacja HistGradientBoosting; większa ogranicza przeuczenie.
    "catboost_depth": 6,  # Głębokość CatBoost; ma znaczenie po jego instalacji.
    "catboost_l2_leaf_reg": 5.0,  # Regularyzacja CatBoost.
    "threads": 4,  # Liczba wątków CPU używanych w treningu.
    "random_state": 42,  # Ziarno losowości: powtarzalność porównań w tym samym środowisku.
    "weather_variants": ["kalendarz", "temperatura", "slonce", "temperatura_slonce", "pogoda_podstawowa", "pogoda_pelna"],
    "importance_rows": 1500,  # Próbka do ważności grup pogody. 0 wyłącza tę analizę.

    # Podział czasowy: trening → walidacja wyboru → osobny test jakości.
    "start_date": "2024-07-01",  # Początek używanej historii energii.
    "include_dst_days": False,  # False = pomijaj całe doby zmiany czasu (23 i 25 h).
    "validation_days": 14,  # Okno doboru modelu/pogody/parametrów.
    "test_days": 14,  # Późniejszy test; NIE służy do strojenia.
    "label_delay_hours": 72,  # Embargo; nie odtwarza publikacji korekt po miesiącach.
    "horizon_hours": 72,  # Zakres, którego uczy się model i który można później prognozować.
    "origin_step_hours": 12,  # Co ile godzin symulujemy historyczne uruchomienie.
    "min_train_rows": 200,  # Minimum zleceń treningowych kierunku.
    "max_train_rows": 400_000,  # Limit najnowszych zleceń kierunku; nie limit rozmiaru Excela.
    "min_evaluation_targets": 24,  # Minimum godzin KAŻDEGO oddziału/kierunku w każdym oknie oceny.
    "portfolio_grace_days": 14,  # Po tylu dniach braku MDD oznaczamy portfel do sprawdzenia.
}

# Opcjonalne filtry SQL; ustaw zgodnie z hurtownią, jeśli istnieje kilka źródeł.
POGODA_SQL = {"sql_owner": None, "sql_weather_type": None}


if __name__ == "__main__":
    raise SystemExit(configured_launch(
        training=True, energy=SCIEZKA_ENERGII, artifacts_dir=FOLDER_MODELU,
        weather_file=SCIEZKA_POGODY, mapping_file=SCIEZKA_MAPOWANIA,
        options=PARAMETRY, weather_options=POGODA_SQL,
    ))
