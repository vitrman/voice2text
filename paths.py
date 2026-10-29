"""Пути приложения.

В сборке exe ресурсы лежат рядом с ним, а данные (настройки, модель, логи,
история) — в %LOCALAPPDATA%\\voice2text, потому что Program Files только для чтения.
Если каталог данных недоступен на запись (например, создан при запуске от
администратора), уходим во временный каталог, чтобы приложение не падало.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

def _data_root() -> Path:
    """%LOCALAPPDATA% может быть пустым — тогда не пишем внутрь папки программы."""
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        return Path(local)
    profile = (os.environ.get("USERPROFILE") or "").strip()
    if profile:
        return Path(profile) / "AppData" / "Local"
    return Path(tempfile.gettempdir())


if FROZEN:
    ROOT = Path(sys.executable).resolve().parent
    DATA = _data_root() / "voice2text"
else:
    ROOT = Path(__file__).resolve().parent
    DATA = ROOT

FALLBACK = False


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


if not _writable(DATA):
    FALLBACK = True
    DATA = Path(tempfile.gettempdir()) / "voice2text"
    DATA.mkdir(parents=True, exist_ok=True)
