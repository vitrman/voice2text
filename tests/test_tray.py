"""Трей: иконка создаётся, окно прячется в трей и показывается обратно."""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


def pump(root: tk.Tk, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.05)


def main() -> int:
    root = tk.Tk()
    instance = app_module.App(root)
    instance.start_hidden = True

    pump(root, 2.0)  # ждём tray_ready
    print(f"иконка создана: {instance.tray_ready}, окно: {root.state()}")

    instance.hide_to_tray()
    hidden = root.state() in ("iconic", "withdrawn")
    print(f"свернуть: {hidden} ({root.state()})")

    instance._show_window()
    shown = root.state() == "normal"
    print(f"показать окно: {shown}")

    instance._quit()
    print("выход: ok")
    return 0 if (hidden and shown) else 1


if __name__ == "__main__":
    sys.exit(main())
