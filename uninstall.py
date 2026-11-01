"""Удаление программы: ярлыки, реестр, данные и папка.

Вызывается как `Voice2Text.exe --uninstall` из «Установка и удаление программ».
Раньше это делал `Uninstall.cmd`, но имя ярлыка у нас русское («Голосовой ввод»),
и cmd читал путь в чужой кодировке — ярлык оставался. Здесь все пути идут через
WinAPI и Python, где кодировка не участвует.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import messagebox

from paths import DATA

APP_TITLE = "Голосовой ввод"
APP_NAME = "Voice2Text"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def _shell_folder(csidl: int) -> Path | None:
    buffer = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0:
        return Path(buffer.value)
    return None


def shortcuts() -> list[Path]:
    """Ярлыки, которые создаёт установщик."""
    result: list[Path] = []
    programs = _shell_folder(0x0002)          # CSIDL_PROGRAMS
    if programs:
        result.append(programs / f"{APP_TITLE}.lnk")
    desktop = _shell_folder(0x0010)          # CSIDL_DESKTOPDIRECTORY
    if desktop:
        result.append(desktop / f"{APP_TITLE}.lnk")
    return result


def _app_running() -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, "Local\\voice2text-single")
    running = kernel32.GetLastError() == 183   # ERROR_ALREADY_EXISTS
    if handle:
        kernel32.CloseHandle(handle)
    return running


def _data_size() -> int:
    total = 0
    for path in DATA.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _clear_program_dir(target: Path) -> bool:
    """Удаляет содержимое папки, кроме работающего exe: так видно, что права есть."""
    failed = 0
    for path in sorted(target.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            if path.is_file() and path.name.lower() != "voice2text.exe":
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        except OSError:
            failed += 1
    return failed == 0


def _remove_registry() -> None:
    for key, value in ((UNINSTALL_KEY, None), (RUN_KEY, APP_NAME)):
        try:
            if value is None:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            else:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                    winreg.DeleteValue(handle, value)
        except OSError:
            continue


def run_uninstall() -> int:
    target = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    if _app_running():
        messagebox.showwarning(
            "Удаление",
            "Приложение запущено. Закройте его (меню иконки в трее → «Выход») "
            "и запустите удаление ещё раз.",
            parent=root)
        root.destroy()
        return 1

    megabytes = _data_size() / 2 ** 20
    question = (
        f"Удалить «{APP_TITLE}»?\n\n"
        f"Будут удалены программа, настройки, история диктовок, журналы "
        f"и скачанная модель GigaAM ({megabytes:.0f} МБ).\n\n"
        f"Папка: {target or DATA}"
    )
    if not messagebox.askyesno("Удаление", question, icon="warning", default="no", parent=root):
        root.destroy()
        return 1

    for link in shortcuts():
        try:
            link.unlink(missing_ok=True)
        except OSError:
            pass
    _remove_registry()

    cleared = _clear_program_dir(target) if target is not None else True
    if not cleared:
        messagebox.showwarning(
            "Удаление",
            "Не всё удалилось: часть файлов занята другой программой.\n"
            "Закройте приложение и повторите удаление.",
            parent=root)
        root.destroy()
        return 1

    try:
        shutil.rmtree(DATA, ignore_errors=True)
    except OSError:
        pass

    if target is not None:
        # папку снимаем после выхода процесса: exe пока запущен
        subprocess.Popen(f'cmd /c ping -n 3 127.0.0.1 >nul & rmdir /s /q "{target}"',
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                         close_fds=True)
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(run_uninstall())
