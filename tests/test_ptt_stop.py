"""Режим «удерживать»: стоп по отпусканию клавиш фактической комбинации.

Проверяем главную ошибку 0.8.5.1: остановка была зашита на Ctrl+Alt+Space, поэтому
с любым другим хоткеем запись висела до лимита и второе нажатие не срабатывало.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import customtkinter as ctk
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


class DummyEngine:
    def __init__(self, *args, **kwargs):
        self.name = "dummy"

    def load(self) -> None:
        pass

    def transcribe(self, audio, **kwargs) -> str:
        return ""

    def dispose(self) -> None:
        pass


class FakeUser32:
    """Клавиши «зажаты» первые 0.4 с, дальше отпущены; остальное — у настоящего USER32."""

    def __init__(self, real) -> None:
        self.real = real
        self.started = time.monotonic()
        self.queried: set[int] = set()

    def __getattr__(self, name):
        return getattr(self.real, name)

    def GetAsyncKeyState(self, vk: int) -> int:
        self.queried.add(vk)
        return 0x8000 if time.monotonic() - self.started < 0.4 else 0

    def GetForegroundWindow(self):
        return 0

    def IsWindow(self, hwnd):
        return 0

    def IsIconic(self, hwnd):
        return 0


def main() -> int:
    app_module.GigaAMEngine = DummyEngine
    app_module.App._setup_tray = lambda self: False   # трей под песочницей недоступен
    root = ctk.CTk()
    root.withdraw()
    instance = app_module.App(root, app_module.Config({"hotkey_mode": "hold"}))

    def slow_record(**kwargs):
        time.sleep(1.0)
        return np.zeros(16000, np.int16)

    instance.recorder.record = slow_record

    fake = FakeUser32(app_module.USER32)
    app_module.USER32 = fake
    time.sleep(0.3)                       # даём слушателю зарегистрировать комбинации
    app_module.HOTKEYS.set_actual("dictate", "alt+f9")

    fake.started = time.monotonic()       # отсчёт «нажатия» начинается здесь
    instance.start_push_to_talk()
    time.sleep(0.2)
    started = instance.recording and instance.stop_event is not None and not instance.stop_event.is_set()
    print(f"запись идёт: {started}")

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not instance.stop_event.is_set():
        time.sleep(0.05)
    stopped = instance.stop_event.is_set()
    print(f"стоп по отпусканию alt+f9: {stopped}")
    print(f"опрошены клавиши: {sorted(fake.queried)}")

    # alt (0x12) и f9 (0x78): ни Ctrl (0x11), ни Space (0x20) в комбинации нет
    keys_ok = fake.queried == {0x12, 0x78}

    # второе нажатие во время записи тоже должно останавливать
    instance.recording = True
    instance.busy = True
    instance.stop_event = __import__("threading").Event()
    instance.start_push_to_talk()
    second = instance.stop_event.is_set()
    print(f"второе нажатие останавливает: {second}")

    instance.recording = False
    instance.busy = False
    instance._quit()
    ok = started and stopped and keys_ok and second
    print("результат:", "ok" if ok else "ОШИБКА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
