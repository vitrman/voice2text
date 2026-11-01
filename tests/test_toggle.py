"""Режим переключателя: первое нажатие старт, второе — стоп."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


class DummyEngine:
    """Без torch и модели: проверяем только логику переключателя."""

    def __init__(self, *args, **kwargs):
        self.name = "dummy"

    def load(self) -> None:
        pass

    def transcribe(self, audio, **kwargs) -> str:
        return ""

    def dispose(self) -> None:
        pass


def main() -> int:
    app_module.GigaAMEngine = DummyEngine
    app_module.App._setup_tray = lambda self: False   # трей под песочницей недоступен
    root = ctk.CTk()
    root.withdraw()
    instance = app_module.App(root, app_module.Config({"hotkey_mode": "toggle"}))

    def slow_record(path, **kwargs):
        time.sleep(5)
        return path

    instance.recorder.record_to_file = slow_record
    instance.queue.submit = lambda *args, **kwargs: None

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
