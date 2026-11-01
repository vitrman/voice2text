"""Автозапуск при входе в Windows.

Ключ `HKCU\\...\\Run` не требует прав администратора, виден в «Диспетчере задач →
Автозагрузка» и снимается пользователем вручную. Путь самовосстанавливается: если
приложение переустановили в другую папку, старая запись указывает в никуда.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Voice2Text"
ARGUMENT = "--minimized"


def executable() -> Path | None:
    """Путь к exe; None, если приложение запущено из исходников."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None


def command() -> str:
    exe = executable()
    return f'"{exe}" {ARGUMENT}' if exe else ""


def available() -> bool:
    return executable() is not None


def current() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, VALUE_NAME)[0])
    except OSError:
        return ""


def enabled() -> bool:
    return bool(current())


def set_enabled(flag: bool) -> None:
    if flag:
        exe = executable()
        if exe is None:
            raise OSError("автозапуск доступен только для установленной версии")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except OSError:
        pass


def sync() -> bool:
    """Обновляет устаревший путь. True — запись была исправлена."""
    value = current()
    if not value or not available():
        return False
    if value == command():
        return False
    try:
        set_enabled(True)
    except OSError:
        return False
    return True
