"""Установщик Voice2Text: распаковка, ярлыки, запись в «Установка и удаление программ».

Сборка: installer/build_installer.py (PyInstaller, payload.zip внутри exe).
Тихий режим для проверки: setup.exe /S /D=<папка> /NOICONS /NOREG /NORUN
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Voice2Text"
APP_TITLE = "Голосовой ввод"
EXE_NAME = "Voice2Text.exe"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
CREATE_NO_WINDOW = 0x08000000

BG = "#121316"
CARD = "#1b1d21"
BORDER = "#2e3238"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#3d78e6"
TEXT = "#f2f3f5"
MUTED = "#8b9199"
ERROR = "#e0554f"


def app_version() -> str:
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    for candidate in (here / "version.txt", Path(sys.executable).parent / "version.txt"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            continue
    return "0.0.0"


def payload_file() -> Path:
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    for candidate in (here / "payload.zip", Path(sys.executable).parent / "payload.zip"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("payload.zip не найден рядом с установщиком")


def default_target() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_NAME


def _shell_folder(csidl: int) -> Path | None:
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
        return Path(buffer.value)
    return None


def start_menu_dir() -> Path | None:
    return _shell_folder(0x0002)      # CSIDL_PROGRAMS


def desktop_dir() -> Path | None:
    return _shell_folder(0x0010)      # CSIDL_DESKTOPDIRECTORY


def make_shortcut(link: Path, target: Path, workdir: Path, icon: Path) -> None:
    script = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        "$s.TargetPath='{target}';"
        "$s.WorkingDirectory='{workdir}';"
        "$s.IconLocation='{icon}';"
        "$s.Description='{desc}';"
        "$s.Save()"
    ).format(link=link, target=target, workdir=workdir, icon=icon, desc=APP_TITLE)
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                   creationflags=CREATE_NO_WINDOW, check=False)


def write_uninstall_script(target: Path, shortcuts: list[Path]) -> Path:
    script = target / "Uninstall.cmd"
    lines = ["@echo off"]
    for link in shortcuts:
        lines.append(f'del "{link}" >nul 2>&1')
    lines.append(f'reg delete "HKCU\\{UNINSTALL_KEY}" /f >nul 2>&1')
    lines.append(f'start "" /min cmd /c rmdir /s /q "{target}"')
    script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return script


def register_uninstall(target: Path, uninstall: Path, exe: Path, version: str) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, f"{APP_TITLE} (GigaAM)")
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(target))
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "GigaAM (локальная сборка)")
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstall}"')
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, int(size / 1024))


def extract(payload: Path, target: Path, progress) -> None:
    with zipfile.ZipFile(payload) as archive:
        names = archive.infolist()
        total = len(names) or 1
        root = target.resolve()
        for index, item in enumerate(names, 1):
            destination = (target / item.filename).resolve()
            if not str(destination).startswith(str(root)):
                raise RuntimeError(f"недопустимый путь в архиве: {item.filename}")
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, open(destination, "wb") as out:
                shutil.copyfileobj(source, out, 1024 * 256)
            progress(index / total, f"Распаковка: {item.filename}")


def install(target: Path, *, icons: bool, registry: bool, progress) -> Path:
    payload = payload_file()
    version = app_version()
    target.mkdir(parents=True, exist_ok=True)
    # Папку не удаляем: при обновлении ставим поверх, иначе занятые файлы ломают установку.
    old_exe = target / (EXE_NAME + ".old")
    if (target / EXE_NAME).exists():
        try:
            old_exe.unlink(missing_ok=True)
            (target / EXE_NAME).rename(old_exe)
        except PermissionError as exc:
            raise RuntimeError("Приложение запущено. Закройте его и повторите установку.") from exc
    try:
        extract(payload, target, progress)
    except Exception:
        # не оставляем пользователя без приложения, если распаковка сорвалась
        if old_exe.exists() and not (target / EXE_NAME).exists():
            old_exe.rename(target / EXE_NAME)
        raise
    old_exe.unlink(missing_ok=True)
    progress(1.0, "Файлы распакованы")
    exe = target / EXE_NAME
    shortcuts: list[Path] = []
    if icons:
        start = start_menu_dir()
        if start:
            link = start / f"{APP_TITLE}.lnk"
            make_shortcut(link, exe, target, exe)
            shortcuts.append(link)
        desktop = desktop_dir()
        if desktop:
            link = desktop / f"{APP_TITLE}.lnk"
            make_shortcut(link, exe, target, exe)
            shortcuts.append(link)
        progress(0.98, "Ярлыки созданы")
    uninstall = write_uninstall_script(target, shortcuts)
    if registry:
        register_uninstall(target, uninstall, exe, version)
    progress(1.0, "Готово")
    return exe


def log_line(message: str) -> None:
    """В windowed-сборке нет stdout: пишем и в консоль (если есть), и в лог."""
    try:
        if sys.stdout is not None:
            print(message, flush=True)
    except Exception:
        pass
    try:
        path = Path(os.environ.get("TEMP", ".")) / "voice2text-setup.log"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


def run_silent(arguments: list[str]) -> int:
    target = default_target()
    icons = registry = launch = True
    for arg in arguments:
        # путь может прийти в кавычках: "/D=C:\Program Files\Voice2Text"
        arg = arg.strip('"')
        low = arg.lower()
        if low.startswith("/d="):
            target = Path(arg[3:].strip('"'))
        elif low == "/noicons":
            icons = False
        elif low == "/noreg":
            registry = False
        elif low == "/norun":
            launch = False
    log_line(f"тихая установка: target={target} icons={icons} reg={registry} run={launch}")
    try:
        exe = install(target, icons=icons, registry=registry,
                      progress=lambda part, text: log_line(f"{part * 100:3.0f}% {text}"))
    except Exception as exc:
        log_line(f"ошибка: {exc!r}")
        return 1
    log_line(f"готово: {exe}")
    if launch:
        subprocess.Popen([str(exe)], creationflags=CREATE_NO_WINDOW)
    return 0


class Setup(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Установка — {APP_TITLE} {app_version()}")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.geometry("620x360")
        self.version = app_version()
        self._build()

    def _build(self) -> None:
        pad = 22
        tk.Label(self, text=f"{APP_TITLE}", bg=BG, fg=TEXT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=pad, pady=(pad, 0))
        tk.Label(self, text=f"GigaAM · установщик {self.version}", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=pad, pady=(2, 16))

        card = tk.Frame(self, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", padx=pad)
        tk.Label(card, text="Куда установить", bg=CARD, fg=TEXT,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=14, pady=(12, 2))
        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=14, pady=(0, 12))
        self.path_var = tk.StringVar(value=str(default_target()))
        entry = tk.Entry(row, textvariable=self.path_var, bg=BG, fg=TEXT, insertbackground=TEXT,
                         relief="flat", highlightthickness=1, highlightbackground=BORDER,
                         highlightcolor=ACCENT, font=("Segoe UI", 10))
        entry.pack(side="left", fill="x", expand=True, ipady=6)
        tk.Button(row, text="Обзор", bg=BG, fg=TEXT, relief="flat", activebackground=CARD,
                  highlightthickness=1, highlightbackground=BORDER, padx=12,
                  command=self._browse).pack(side="left", padx=(8, 0), ipady=4)

        options = tk.Frame(self, bg=BG)
        options.pack(fill="x", padx=pad, pady=(12, 0))
        self.run_var = tk.BooleanVar(value=True)
        for text, var in (("Запустить после установки", self.run_var),):
            tk.Checkbutton(options, text=text, variable=var, bg=BG, fg=TEXT,
                           selectcolor=CARD, activebackground=BG, activeforeground=TEXT,
                           font=("Segoe UI", 10)).pack(anchor="w")

        self.status = tk.StringVar(value="Будет скачана модель GigaAM (~430 МБ) при первом запуске.")
        tk.Label(self, textvariable=self.status, bg=BG, fg=MUTED, anchor="w",
                 font=("Segoe UI", 9), wraplength=560, justify="left").pack(
            fill="x", padx=pad, pady=(10, 6))

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=pad)

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=pad, pady=(14, pad))
        self.install_button = tk.Button(buttons, text="Установить", bg=ACCENT, fg="white",
                                        relief="flat", activebackground=ACCENT_HOVER,
                                        activeforeground="white", padx=26, pady=8,
                                        font=("Segoe UI", 10, "bold"), command=self._start)
        self.install_button.pack(side="right")
        tk.Button(buttons, text="Отмена", bg=BG, fg=TEXT, relief="flat",
                  activebackground=CARD, activeforeground=TEXT, padx=18, pady=8,
                  highlightthickness=1, highlightbackground=BORDER,
                  font=("Segoe UI", 10), command=self.destroy).pack(side="right", padx=(0, 10))

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(default_target().parent))
        if chosen:
            self.path_var.set(chosen)

    def _start(self) -> None:
        target = Path(self.path_var.get().strip() or default_target())
        try:
            payload_file()
        except FileNotFoundError as exc:
            messagebox.showerror("Установка", str(exc), parent=self)
            return
        self.install_button.configure(state="disabled")
        threading.Thread(target=self._worker, args=(target,), daemon=True).start()

    def _worker(self, target: Path) -> None:
        def progress(part: float, text: str) -> None:
            self.after(0, lambda: (self.progress.configure(value=part * 100),
                                   self.status.set(text)))

        try:
            exe = install(target, icons=True, registry=True, progress=progress)
        except Exception as exc:  # показываем причину, а не молчаливый выход
            self.after(0, lambda: messagebox.showerror("Установка", f"Не удалось: {exc}",
                                                       parent=self))
            self.after(0, lambda: self.install_button.configure(state="normal"))
            return
        self.after(0, lambda: self.status.set(f"Установлено в {target}"))
        if self.run_var.get():
            subprocess.Popen([str(exe)])
        self.after(0, self.destroy)


def main() -> int:
    arguments = sys.argv[1:]
    if any(arg.lower() in ("/s", "-s", "--silent") for arg in arguments):
        return run_silent(arguments)
    Setup().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
