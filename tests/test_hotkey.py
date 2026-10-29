"""Проверка горячих клавиш: регистрация и доставка WM_HOTKEY в поток слушателя."""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def main() -> int:
    heard: list[str] = []
    app.start_hotkey_listener(lambda: heard.append("dictate"),
                              lambda: heard.append("show"))
    time.sleep(0.5)

    tid = app.HOTKEY_TID["tid"]
    if not tid:
        print("ОШИБКА: поток слушателя не запустился")
        return 1

    ctypes.windll.user32.PostThreadMessageW(tid, app.WM_HOTKEY, 1, 0)
    ctypes.windll.user32.PostThreadMessageW(tid, app.WM_HOTKEY, 2, 0)
    time.sleep(0.6)
    print(f"события: {heard}")
    return 0 if heard == ["dictate", "show"] else 1


if __name__ == "__main__":
    sys.exit(main())
