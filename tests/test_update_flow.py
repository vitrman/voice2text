"""Проверки обновления без сети: кэш, отмена, скрипт-помощник, установка поверх."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import update  # noqa: E402
from installer import setup_app  # noqa: E402


def test_cache_and_cancel() -> None:
    folder = update.cache_dir()
    name = "Voice2Text-9.9.9-installer.zip"
    path = folder / name
    path.write_bytes(b"x" * 100)
    assert update.cached(name, 100) is not None
    assert update.cached(name, 200) is None
    path.unlink()

    cancel = threading.Event()
    cancel.set()

    class Slow:
        headers: dict = {}

        def read(self, size: int = -1) -> bytes:
            cancel.set()
            return b"y" * 1024

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    original = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: Slow()
    try:
        try:
            update.download("http://x", name, cancel=cancel)
        except update.UpdateCancelled:
            pass
        else:
            raise AssertionError("отмена не сработала")
    finally:
        urllib.request.urlopen = original
    assert not (folder / (name + ".part")).exists()
    print("кэш и отмена: ок")


def test_apply_helper() -> None:
    work = Path(tempfile.gettempdir()) / "upd-apply-test"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    archive = work / "Voice2Text-9.9.9-installer.zip"
    with zipfile.ZipFile(archive, "w") as archive_file:
        archive_file.writestr("Voice2Text-9.9.9-setup/setup.exe", b"MZ")
    target = work / "app"
    target.mkdir()
    (target / "Voice2Text.exe").write_bytes(b"MZ")

    captured = {}
    original = subprocess.Popen

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["flags"] = kwargs.get("creationflags")
        return object()

    subprocess.Popen = fake_popen
    try:
        helper = update.apply(archive, target, pid=424242)
    finally:
        subprocess.Popen = original
    text = helper.read_text(encoding="utf-8")
    assert "PID eq 424242" in text, "скрипт не ждёт завершения приложения"
    assert "/S /D=" in text, "нет тихой установки"
    assert 'start ""' in text, "нет перезапуска"
    assert captured["flags"], "процесс запущен без флагов отсоединения"
    print("скрипт-помощник: ок")


def test_install_over_existing() -> None:
    work = Path(tempfile.gettempdir()) / "upd-install-test"
    shutil.rmtree(work, ignore_errors=True)
    target = work / "app"
    target.mkdir(parents=True)
    (target / "Voice2Text.exe").write_bytes(b"MZ-old")
    (target / "stale.txt").write_text("old", encoding="utf-8")
    payload = work / "payload.zip"
    with zipfile.ZipFile(payload, "w") as archive_file:
        archive_file.writestr("Voice2Text.exe", b"MZ-new")
        archive_file.writestr("_internal/lib.txt", "ok")

    setup_app.payload_file = lambda: payload
    setup_app.app_version = lambda: "9.9.9"
    setup_app.install(target, icons=False, registry=False, progress=lambda part, text: None)
    assert (target / "Voice2Text.exe").read_bytes() == b"MZ-new", "exe не заменился"
    assert (target / "_internal/lib.txt").is_file(), "новые файлы не распакованы"
    assert not (target / "Voice2Text.exe.old").exists(), "остался .old"
    print("установка поверх: ок")


if __name__ == "__main__":
    test_cache_and_cancel()
    test_apply_helper()
    test_install_over_existing()
    print("все проверки обновления прошли")
