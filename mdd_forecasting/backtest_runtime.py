"""Uruchomienie backtestu, domyślnie bez limitu czasu, także na Windows."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def atomic_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def exclusive_run(directory: Path):
    """Blokada systemowa znika również po awarii; nie zgadujemy stanu procesu po PID."""
    with (directory / ".backtest.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Inny backtest korzysta z tego folderu. Poczekaj lub wybierz inny FOLDER_BACKTESTU.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def supervise(command: list[str], directory: Path, timeout_seconds: float | None = None) -> int:
    """None oznacza pracę do końca. Opcjonalny limit obejmuje cały proces."""
    if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("Limit czasu musi być dodatnią, skończoną liczbą.")
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=ROOT)
    timed_out = False
    try:
        while True:
            completed = process.poll()
            if completed is not None:
                return completed
            remaining = None if timeout_seconds is None else timeout_seconds - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                timed_out = True
                break
            try:
                return process.wait(timeout=5 if remaining is None else min(5, remaining))
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if timed_out:
            status_path = directory / "status_backtestu.json"
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                status = {}
            status.update(etap="LIMIT_CZASU", kompletny=False, czas_s=time.monotonic() - started,
                          komunikat="Przerwano po limicie. Gotowe raporty są częściowe. Wznów z tymi samymi ustawieniami.")
            atomic_json(status_path, status)
            print(f"LIMIT CZASU. Wynik niekompletny: {directory}. Wznów, aby wykorzystać zapisane próby/model.", flush=True)
    return 124


def configured_backtest(*, energy: str, weather_file: str, mapping_file: str, output_dir: str,
                        train_from: str, train_to: str, backtest_from: str, backtest_to: str,
                        parameters: dict, weather_options: dict, runtime: dict) -> int:
    from .branch_launcher import _path
    from .branch_database import DEFAULT_BRANCH_SQL
    from .config import DEFAULT_MAPPING_PATH

    root = None
    try:
        if not str(energy).strip():
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            energy = filedialog.askopenfilename(parent=root, title="Historia energii do treningu i backtestu",
                                               filetypes=[("Excel i CSV", "*.xlsx *.xlsm *.csv")])
            if not energy:
                return 0
        energy_path = Path(_path(energy))
        weather_path = Path(_path(weather_file)) if str(weather_file).strip() else DEFAULT_BRANCH_SQL
        mapping_path = Path(_path(mapping_file)) if str(mapping_file).strip() else DEFAULT_MAPPING_PATH
        for path in (energy_path, weather_path, mapping_path):
            if not path.is_file():
                raise FileNotFoundError(f"Nie znaleziono pliku: {path}")
        value = runtime.get("max_runtime_hours")
        hours = None if value is None else float(value)
        if hours is not None and (not math.isfinite(hours) or hours <= 0):
            raise ValueError("max_runtime_hours musi być dodatnią, skończoną liczbą.")
        output = Path(_path(output_dir))
        output.mkdir(parents=True, exist_ok=True)
        with exclusive_run(output):
            run = output / datetime.now().strftime("backtest_%Y%m%d_%H%M%S_%f")
            run.mkdir()
            configuration = {
                "energy": str(energy_path), "weather_file": str(weather_path), "mapping": str(mapping_path),
                "run_dir": str(run), "cache_root": str(output / "cache"),
                "train_from": train_from, "train_to": train_to,
                "backtest_from": backtest_from, "backtest_to": backtest_to,
                "parameters": dict(parameters), "weather_options": dict(weather_options), "runtime": dict(runtime),
            }
            config_path = run / "ustawienia_backtestu.json"
            atomic_json(config_path, configuration)
            (output / "OSTATNI_BACKTEST.txt").write_text(str(run.resolve()), encoding="utf-8")
            timing = "Bez limitu czasu — obliczenia działają do zakończenia." if hours is None else f"Limit całego zadania: {hours:g} h."
            print(f"Backtest: {run}\n{timing}", flush=True)
            return supervise([sys.executable, "-u", "-m", "mdd_forecasting.backtest", "--config", str(config_path)],
                             run, None if hours is None else hours * 3600)
    finally:
        if root is not None:
            root.destroy()
