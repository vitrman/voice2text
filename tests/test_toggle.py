"""Режим переключателя: первое нажатие старт, второе — стоп."""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    instance = app_module.App(root, app_module.Config({"hotkey_mode": "toggle"}))
    instance._record_and_transcribe = lambda *args, **kwargs: time.sleep(5)

    instance.on_hotkey()
    time.sleep(0.1)
    started = instance.recording and instance.stop_event is not None
    print(f"старт по нажатию: {started}")

    instance.on_hotkey()
    time.sleep(0.1)
    stopped = instance.stop_event is None or instance.stop_event.is_set()
    print(f"стоп по второму нажатию: {stopped}")

    instance._quit()
    return 0 if (started and stopped) else 1


if __name__ == "__main__":
    sys.exit(main())
