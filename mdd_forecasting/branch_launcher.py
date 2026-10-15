"""Wspólne okna dwóch osobnych launcherów Windows/VS Code."""
from __future__ import annotations
from pathlib import Path
import traceback
from datetime import datetime


ROOT = Path(__file__).resolve().parents[1]


def _path(value: str | Path) -> str:
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else ROOT / path)


def _options(settings: dict | None) -> list[str]:
    arguments = []
    for name, value in (settings or {}).items():
        if value is None or value is False:
            continue
        option = "--" + name.replace("_", "-")
        arguments.append(option)
        if value is not True:
            if isinstance(value, (list, tuple)):
                arguments.extend(str(item) for item in value)
            else:
                arguments.append(str(value))
    return arguments


def configured_launch(training: bool, energy: str = "", artifacts_dir: str = "model_bilansu",
                      weather_file: str = "", output_dir: str = "", options: dict | None = None,
                      weather_options: dict | None = None, as_of: str = "", mapping_file: str = "") -> int:
    """Ścieżki ustawione w kodzie omijają GUI; względne są względem repo, nie cwd VS Code."""
    from .branch_database import DEFAULT_BRANCH_SQL
    from .branch_cli import train_main, predict_main
    from .branch_data import EnergyTimeError
    energy = str(energy).strip()
    artifacts_dir = str(artifacts_dir).strip()
    weather_file = str(weather_file).strip()
    root = None
    try:
        if (training and not energy) or not artifacts_dir:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if training and not energy:
                energy = filedialog.askopenfilename(parent=root, title="Wybierz historię energii A:I",
                    filetypes=[("Excel i CSV", "*.xlsx *.xlsm *.csv"), ("Wszystkie pliki", "*.*")])
                if not energy:
                    return 0
            if not artifacts_dir:
                artifacts_dir = filedialog.askdirectory(parent=root, title="Folder zapisanych modeli", mustexist=not training)
                if not artifacts_dir:
                    return 0
        if energy and not Path(_path(energy)).is_file():
            raise FileNotFoundError(f"Nie znaleziono pliku energii: {_path(energy)}. Ustaw SCIEZKA_ENERGII na początku programu.")
        if weather_file and not Path(_path(weather_file)).is_file():
            raise FileNotFoundError(f"Nie znaleziono pliku pogody: {_path(weather_file)}.")
        arguments = ["--artifacts-dir", _path(artifacts_dir)]
        if energy:
            arguments += ["--energy", _path(energy)]
        if training and str(mapping_file).strip():
            arguments += ["--mapping", _path(str(mapping_file).strip())]
        if weather_file:
            weather_option = "--weather-sql" if Path(weather_file).suffix.lower() == ".sql" else "--weather"
            arguments += [weather_option, _path(weather_file)]
        else:
            arguments += ["--weather-sql", str(DEFAULT_BRANCH_SQL)]
        arguments += _options(weather_options)
        arguments += _options(options)
        if not training:
            output = _path(output_dir) if output_dir else str(Path(_path(artifacts_dir)) / "prognozy" / datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            arguments += ["--output-dir", output]
            if as_of:
                arguments += ["--as-of", as_of]
        return train_main(arguments) if training else predict_main(arguments)
    except Exception as exc:
        log_dir = Path(_path(artifacts_dir or "model_bilansu"))
        log_dir.mkdir(parents=True, exist_ok=True)
        if isinstance(exc, EnergyTimeError):
            report = log_dir / "bledne_daty_godziny.csv"
            exc.records.to_csv(report, sep=";", decimal=",", index=False, encoding="utf-8-sig")
            print(f"Rekordy z niepoprawną datą/godziną: {report}", flush=True)
        log = log_dir / "blad_bilansow.txt"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        print(f"Błąd. Szczegóły zapisano w {log}", flush=True)
        raise
    finally:
        if root is not None:
            root.destroy()


def launch(training: bool) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from .branch_database import DEFAULT_BRANCH_SQL
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    directory = None
    try:
        if training:
            energy = filedialog.askopenfilename(parent=root, title="Wybierz historię energii A:I",
                                                filetypes=[("Excel i CSV", "*.xlsx *.xlsm *.csv"), ("Wszystkie pliki", "*.*")])
            if not energy:
                return 0
            directory = filedialog.askdirectory(parent=root, title="Wybierz folder na zapis modelu i raportów", mustexist=False)
            if not directory:
                return 0
            from .branch_cli import train_main
            result = train_main(["--energy", energy, "--artifacts-dir", directory,
                                 "--weather-sql", str(DEFAULT_BRANCH_SQL)])
            messagebox.showinfo("Nauka zakończona", f"Model i raporty zapisano w:\n{directory}", parent=root)
        else:
            directory = filedialog.askdirectory(parent=root, title="Wybierz folder z zapisanym modelem")
            if not directory:
                return 0
            output = Path(directory) / "prognozy" / datetime.now().strftime("%Y%m%d_%H%M%S")
            energy = filedialog.askopenfilename(parent=root,
                title="Opcjonalnie: aktualny eksport energii do kontroli klientów. Anuluj = historia treningu",
                filetypes=[("Excel i CSV", "*.xlsx *.xlsm *.csv"), ("Wszystkie pliki", "*.*")])
            from .branch_cli import predict_main
            arguments = ["--artifacts-dir", directory, "--output-dir", str(output),
                         "--weather-sql", str(DEFAULT_BRANCH_SQL)]
            if energy:
                arguments += ["--energy", energy]
            result = predict_main(arguments)
            messagebox.showinfo("Prognoza zakończona", f"Prognoza 72 h:\n{output}\n\n"
                                + ("Są braki pogody. Sprawdź statusy w CSV." if result else ""), parent=root)
        return result
    except Exception as exc:
        log = Path(directory) / "blad_bilansow.txt" if directory else Path.cwd() / "blad_bilansow.txt"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        messagebox.showerror("Błąd modelu", f"{type(exc).__name__}: {exc}\n\nSzczegóły: {log}", parent=root)
        return 1
    finally:
        root.destroy()
