"""Проверка нижнего индикатора: показывается, не забирает фокус, прячется."""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import customtkinter as ctk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


class DummyEngine:
    """Без torch и модели: проверяем только индикатор."""

    def __init__(self, *args, **kwargs):
        self.name = "dummy"

    def load(self) -> None:
        pass

    def transcribe(self, audio, **kwargs) -> str:
        return ""

    def dispose(self) -> None:
        pass


def pump(root: ctk.CTk, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.05)


def main() -> int:
    app_module.GigaAMEngine = DummyEngine
    app_module.App._setup_tray = lambda self: False
    root = ctk.CTk()
    instance = app_module.App(root)
    instance.start_hidden = False

    foreground_before = app_module.USER32.GetForegroundWindow()
    instance._overlay_show("Запись…", "#e05555", 60000)
    pump(root, 1.0)

    visible = bool(app_module.USER32.IsWindowVisible(instance.overlay_hwnd))
    foreground_after = app_module.USER32.GetForegroundWindow()
    print(f"индикатор виден: {visible}")
    print(f"фокус не украден: {foreground_before == foreground_after}")

    try:
        from PIL import ImageGrab
        ImageGrab.grab().save(ROOT / "docs" / "overlay.png")
        print("скриншот: docs/overlay.png")
    except Exception as exc:
        print("скриншот не снялся:", exc)

    instance._overlay_hide()
    pump(root, 0.3)
    print("спрятан:", instance.overlay.state() == "withdrawn")

    instance._quit()
    return 0 if visible else 1


if __name__ == "__main__":
    sys.exit(main())
