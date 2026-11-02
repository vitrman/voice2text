"""Режим переключателя: старт записи действительно запускает поток, стоп работает.

Регрессия 0.9.0.1–0.9.1.1: метод записи `_capture` перекрывался атрибутом
`self._capture` (состояние записи хоткея, изначально None), поток стартовал
с target=None, падал с TypeError, а флаг `recording` оставался включённым —
запись «висела», «Стоп» не срабатывал, индикатор уровня не двигался.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


class DummyEngine:
    """Без torch и модели: проверяем только запуск и остановку записи."""

    def __init__(self, *args, **kwargs):
        self.name = "dummy"

    def load(self) -> None:
        pass

    def transcribe(self, audio, **kwargs) -> str:
        return ""

    def transcribe_file(self, path, **kwargs) -> str:
        return ""

    def dispose(self) -> None:
        pass


def wait(condition, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    app_module.GigaAMEngine = DummyEngine
    app_module.App._setup_tray = lambda self: False   # трей под песочницей недоступен
    root = ctk.CTk()
    root.withdraw()
    instance = app_module.App(root, app_module.Config({"hotkey_mode": "toggle"}))

    calls = {"record": 0}

    def fake_record(path, **kwargs):
        calls["record"] += 1
        stop = kwargs.get("stop_event")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if stop is not None and stop.is_set():
                raise app_module.RecordingStopped()
            time.sleep(0.02)
        return path

    instance.recorder.record_to_file = fake_record
    instance.queue.submit = lambda *args, **kwargs: None

    # метод записи не должен быть перекрыт атрибутом
    method_ok = callable(instance._record_to_queue)

    instance.on_hotkey()
    time.sleep(0.15)
    started = instance.recording and calls["record"] == 1
    thread_alive = instance._record_thread is not None and instance._record_thread.is_alive()
    print(f"старт: recording={instance.recording}, запись вызвана={calls['record']}, "
          f"поток={thread_alive}")

    instance.on_hotkey()
    stopped = wait(lambda: not instance.recording)
    print(f"стоп: recording={instance.recording}, отработал={stopped}")

    # сбой в потоке не должен оставлять запись «включённой»
    def boom(*args):
        raise RuntimeError("тестовый сбой")

    instance._record_to_queue = boom
    instance.recording = False
    instance.toggle_recording()
    crashed_ok = wait(lambda: not instance.recording)
    print(f"сбой потока сбрасывает состояние: {crashed_ok}")

    instance._quit()
    ok = method_ok and started and thread_alive and stopped and crashed_ok
    print("результат:", "ok" if ok else "ОШИБКА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
