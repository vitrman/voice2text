"""Остановка по кнопке: событие стопа реально прекращает запись в файл.

Проверяем именно запись в файл (record_to_file), а не логику кнопки: раньше
тест режима переключателя подменял запись заглушкой и не ловил такие ошибки.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import recorder  # noqa: E402
from test_vad import read_sample  # noqa: E402


class RealTimeStream:
    """Отдаёт речь с той же скоростью, что и микрофон, без конца."""

    def __init__(self, audio: np.ndarray):
        self.audio = audio
        self.pos = 0

    def start(self):
        pass

    def read(self, frames):
        time.sleep(frames / 16000)
        while self.pos + frames > len(self.audio):
            self.pos = 0
        chunk = self.audio[self.pos:self.pos + frames]
        self.pos += frames
        return np.asarray(chunk, dtype=np.float32).reshape(-1, 1), False

    def stop(self):
        pass

    def close(self):
        pass


class StalledStream:
    """Устройство открылось, но звук не отдаёт: read висит навсегда.

    Именно так вела себя запись, когда выбран не тот вход или устройство занято:
    кнопка «Стоп» ставила событие, но проверить его было некому.
    """

    def __init__(self):
        self.released = threading.Event()

    def start(self):
        pass

    def read(self, frames):
        self.released.wait()
        raise OSError("устройство остановлено")

    def stop(self):
        self.released.set()

    def close(self):
        self.released.set()


def run(silence_ms: int, stop_after: float = 1.5) -> float:
    speech = read_sample().astype(np.float32)
    recorder.sd.InputStream = lambda **kwargs: RealTimeStream(speech)
    stop = threading.Event()
    threading.Timer(stop_after, stop.set).start()
    path = Path(tempfile.gettempdir()) / "stop-test.wav"
    recorder.MicRecorder(block_ms=50, log=lambda _: None).record_to_file(
        path, silence_ms=silence_ms, start_timeout=8, stop_event=stop)
    seconds = (path.stat().st_size - 44) / 2 / 16000
    path.unlink(missing_ok=True)
    print(f"silence_ms={silence_ms}: писали {seconds:.1f} с при стопе через {stop_after} с")
    return seconds


def run_stalled() -> int:
    """Зависшее устройство не должно держать запись: стоп срабатывает по таймауту."""
    recorder.sd.InputStream = lambda **kwargs: StalledStream()
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    path = Path(tempfile.gettempdir()) / "stall-test.wav"
    mic = recorder.MicRecorder(block_ms=50, log=lambda _: None)
    mic.read_timeout = 5.0
    started = time.perf_counter()
    result = "ok"
    try:
        mic.record_to_file(path, silence_ms=0, start_timeout=8, stop_event=stop)
        result = "записано без звука"
    except recorder.RecorderError as exc:
        result = f"RecorderError: {exc}"
    elapsed = time.perf_counter() - started
    path.unlink(missing_ok=True)
    print(f"зависшее устройство: {result}, вышли через {elapsed:.1f} с")
    return 0 if elapsed < 3.0 else 1


def run_dead_device() -> int:
    """Без стопа зависшее устройство даёт понятную ошибку, а не бесконечную запись."""
    recorder.sd.InputStream = lambda **kwargs: StalledStream()
    path = Path(tempfile.gettempdir()) / "dead-test.wav"
    mic = recorder.MicRecorder(block_ms=50, log=lambda _: None)
    mic.read_timeout = 0.5
    started = time.perf_counter()
    try:
        mic.record_to_file(path, silence_ms=0, start_timeout=8)
    except recorder.RecorderError as exc:
        elapsed = time.perf_counter() - started
        print(f"мёртвое устройство: {exc} (через {elapsed:.1f} с)")
        path.unlink(missing_ok=True)
        return 0 if "не отдаёт звук" in str(exc) else 1
    path.unlink(missing_ok=True)
    print("ОШИБКА: зависшее устройство не дало ошибки")
    return 1


def run_slow_open() -> int:
    """Драйвер завис на открытии: приложение не должно ждать вечно."""
    class SlowOpen:
        def __init__(self, **kwargs):
            pass

        def start(self):
            time.sleep(5)

        def read(self, frames):
            return np.zeros((frames, 1), np.float32), False

        def stop(self):
            pass

        def close(self):
            pass

    recorder.sd.InputStream = lambda **kwargs: SlowOpen()
    mic = recorder.MicRecorder(block_ms=50, log=lambda _: None)
    mic.open_timeout = 0.3
    path = Path(tempfile.gettempdir()) / "slow-open.wav"
    started = time.perf_counter()
    try:
        mic.record_to_file(path, silence_ms=0, start_timeout=8)
    except recorder.RecorderError as exc:
        elapsed = time.perf_counter() - started
        print(f"зависшее открытие: {exc} (через {elapsed:.1f} с)")
        path.unlink(missing_ok=True)
        return 0 if elapsed < 4.0 else 1
    path.unlink(missing_ok=True)
    print("ОШИБКА: зависшее открытие не дало ошибки")
    return 1


def run_stop_while_open() -> int:
    """«Стоп» во время открытия устройства тоже должен срабатывать."""
    class SlowOpen:
        def __init__(self, **kwargs):
            pass

        def start(self):
            time.sleep(5)

        def read(self, frames):
            return np.zeros((frames, 1), np.float32), False

        def stop(self):
            pass

        def close(self):
            pass

    recorder.sd.InputStream = lambda **kwargs: SlowOpen()
    mic = recorder.MicRecorder(block_ms=50, log=lambda _: None)
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    path = Path(tempfile.gettempdir()) / "stop-open.wav"
    started = time.perf_counter()
    try:
        mic.record_to_file(path, silence_ms=0, start_timeout=8, stop_event=stop)
    except recorder.RecorderError:
        elapsed = time.perf_counter() - started
        print(f"стоп во время открытия: вышли через {elapsed:.1f} с")
        path.unlink(missing_ok=True)
        return 0 if elapsed < 2.0 else 1
    path.unlink(missing_ok=True)
    print("ОШИБКА: стоп во время открытия не сработал")
    return 1


def main() -> int:
    for silence_ms in (0, 700):
        seconds = run(silence_ms)
        if seconds > 3.0:
            print("ОШИБКА: запись не остановилась по событию стопа")
            return 1
    if run_stalled():
        return 1
    if run_dead_device():
        return 1
    if run_slow_open():
        return 1
    if run_stop_while_open():
        return 1
    print("остановка и таймауты устройства: ок")
    return 0


if __name__ == "__main__":
    sys.exit(main())
