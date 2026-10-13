"""Wspólne okna dwóch osobnych launcherów Windows/VS Code."""
from __future__ import annotations
from pathlib import Path
import traceback
from datetime import datetime


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
