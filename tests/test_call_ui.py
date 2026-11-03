"""Вкладка «Созвон»: старт, стоп, метаданные и блокировка диктовки."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import calls  # noqa: E402


class DummyEngine:
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


def pump(root, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.03)


def main() -> int:
    results: list[bool] = []
    work = Path(tempfile.gettempdir()) / "call-ui-test"
    shutil.rmtree(work, ignore_errors=True)
    original_root = calls.CALLS_DIR
    calls.CALLS_DIR = work

    app_module.GigaAMEngine = DummyEngine
    app_module.App._setup_tray = lambda self: False
    app_module.loopback.available = lambda: True
    app_module.loopback.device_name = lambda: "Динамики (тест)"

    root = ctk.CTk()
    root.withdraw()
    instance = app_module.App(root, app_module.Config())
    pump(root, 0.3)

    tabs = [name for name in ("Диктовка", "Созвон") if name in instance.tabs._name_list]
    ok = tabs == ["Диктовка", "Созвон"]
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} вкладки: {tabs}")

    transcribed: list[Path] = []
    instance._start_call_transcription = lambda session: transcribed.append(session)
    instance._record_call_mic = lambda session: time.sleep(0.2)
    instance._record_call_system = lambda session: time.sleep(0.2)
    instance.call_threads = []

    instance.toggle_call()
    pump(root, 0.4)
    started = instance.call_active and instance.call_button.cget("text").startswith("■")
    results.append(started)
    print(f"{'ok  ' if started else 'FAIL'} созвон начат, кнопка «Завершить»")

    meta_file = None
    if instance.call_session is not None:
        meta_file = instance.call_session / calls.META_FILE
    meta_ok = bool(meta_file and meta_file.exists())
    if meta_ok:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        meta_ok = meta.get("system_device") == "Динамики (тест)"
    results.append(meta_ok)
    print(f"{'ok  ' if meta_ok else 'FAIL'} meta.json с устройствами")

    instance.toggle_recording()
    blocked = not instance.recording and "созвон" in instance.status.get().lower()
    results.append(blocked)
    print(f"{'ok  ' if blocked else 'FAIL'} диктовка заблокирована: {instance.status.get()!r}")

    instance.toggle_call()
    pump(root, 0.8)
    stopped = not instance.call_active and transcribed
    results.append(bool(stopped))
    print(f"{'ok  ' if stopped else 'FAIL'} стоп передаёт созвон на расшифровку")

    calls.CALLS_DIR = original_root
    shutil.rmtree(work, ignore_errors=True)
    instance._quit()

    ok = all(results)
    print("результат:", "ok" if ok else "ОШИБКА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
