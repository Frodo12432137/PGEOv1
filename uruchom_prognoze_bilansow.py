"""Uruchamiaj kilka razy dziennie; wczytuje modele i nie wykonuje treningu."""
from mdd_forecasting.branch_launcher import launch

if __name__ == "__main__":
    raise SystemExit(launch(training=False))
