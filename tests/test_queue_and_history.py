"""Очередь распознавания, история и запись в файл — без GUI и без модели."""

from __future__ import annotations

import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import history  # noqa: E402
import jobs  # noqa: E402
import recorder  # noqa: E402
from test_vad import FakeStream, read_sample  # noqa: E402


def _wait(condition, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_queue_order() -> None:
    texts: list[str] = []
    queue = jobs.TranscriptionQueue(
        transcribe=lambda path, cancel: (time.sleep(0.05), path.stem)[1],
        on_text=lambda text, task: texts.append(text),
    )
    for name in ("one", "two", "three"):
        queue.submit(Path(f"{name}.wav"))
    assert _wait(lambda: len(texts) == 3), texts
    queue.stop()
    assert texts == ["one", "two", "three"], texts
    print("очередь: порядок сохранён")


def test_queue_cancel() -> None:
    texts: list[str] = []
    started = {"count": 0}

    def slow(path, cancel):
        started["count"] += 1
        for _ in range(100):
            if cancel.is_set():
                raise jobs.TranscriptionCancelled("отменено")
            time.sleep(0.02)
        return "late"

    queue = jobs.TranscriptionQueue(transcribe=slow,
                                    on_text=lambda text, task: texts.append(text))
    for index in range(3):
        queue.submit(Path(f"task{index}.wav"))
    assert _wait(lambda: started["count"] == 1)
    dropped = queue.cancel_all()
    assert dropped >= 2, dropped
    time.sleep(0.4)
    queue.stop()
    assert not texts, texts
    print("очередь: отмена очищает и прерывает")


def test_history_append_and_rotate() -> None:
    folder = Path(tempfile.gettempdir()) / "history-test"
    for old in folder.glob("*"):
        old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    original_path, original_max = history.HISTORY, history.HISTORY_MAX_BYTES
    history.HISTORY, history.HISTORY_MAX_BYTES = folder / "history.log", 200
    try:
        history.append_text("первая фраза")
        history.append_text("вторая\nфраза")
        assert history.HISTORY.exists()
        lines = history.HISTORY.read_text(encoding="utf-8").splitlines()
        assert "\t" in lines[0], lines
        assert lines[0].endswith("первая фраза"), lines
        assert "вторая фраза" in lines[1], lines        # перевод строки схлопнут
        for index in range(10):
            history.append_text("x" * 100 + str(index))
        assert (folder / "history.1.log").exists(), "нет ротации журнала"
    finally:
        history.HISTORY, history.HISTORY_MAX_BYTES = original_path, original_max
        for old in folder.glob("*"):
            old.unlink()
    print("история: запись и ротация")


def test_last_takes_rotation() -> None:
    folder = Path(tempfile.gettempdir()) / "takes-test"
    for old in folder.glob("*"):
        old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    original = history.LAST_DIR
    history.LAST_DIR = folder
    try:
        for index in range(5):
            path = folder / f"take{index}.wav"
            path.write_bytes(b"RIFF")
            stamp = time.time() - (10 - index)
            import os
            os.utime(path, (stamp, stamp))
        removed = history.rotate_takes(keep=3)
        assert len(removed) == 2, removed
        assert len(list(folder.glob("*.wav"))) == 3
    finally:
        history.LAST_DIR = original
        for old in folder.glob("*"):
            old.unlink()
    print("последние записи: ротация")


def test_record_to_file() -> None:
    speech = read_sample()
    audio = np.concatenate([np.zeros(8000, np.float32), speech, np.zeros(24000, np.float32)])
    recorder.sd.InputStream = lambda **kwargs: FakeStream(audio)
    folder = Path(tempfile.gettempdir()) / "record-file-test"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "take.wav"
    try:
        recorder.MicRecorder(log=lambda _: None).record_to_file(
            path, silence_ms=700, start_timeout=5)
        assert path.exists(), "файл не создан"
        with wave.open(str(path), "rb") as handle:
            assert handle.getframerate() == 16000
            assert handle.getnchannels() == 1
            seconds = handle.getnframes() / 16000
        assert seconds > len(speech) / 16000 * 0.9, seconds
        assert seconds < len(speech) / 16000 + 1.3, seconds
        data = recorder.read_wav_int16(path)
        assert data.dtype == np.int16 and len(data) > 0
    finally:
        path.unlink(missing_ok=True)
    print("запись в файл: ок")


if __name__ == "__main__":
    test_queue_order()
    test_queue_cancel()
    test_history_append_and_rotate()
    test_last_takes_rotation()
    test_record_to_file()
    print("все проверки очереди, истории и записи прошли")
