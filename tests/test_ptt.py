"""Push-to-talk: запись идёт до сигнала стопа, а не до паузы в речи."""

from __future__ import annotations

import sys
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
    """Отдаёт аудио с той же скоростью, что и микрофон."""

    def __init__(self, audio: np.ndarray, block: int):
        self.audio = audio
        self.pos = 0
        self.block = block

    def start(self):
        pass

    def read(self, frames):
        time.sleep(frames / 16000)          # ровно столько же, сколько длится звук
        chunk = self.audio[self.pos:self.pos + frames]
        self.pos += frames
        if len(chunk) < frames:
            chunk = np.zeros(frames, np.float32)
        return chunk.reshape(-1, 1), False

    def stop(self):
        pass

    def close(self):
        pass


def main() -> int:
    speech = read_sample()
    stream_audio = np.concatenate([speech, np.zeros(16000 * 5, np.float32)])
    block_ms = recorder.BLOCK_MS
    recorder.sd.InputStream = lambda **kwargs: RealTimeStream(
        stream_audio, int(16000 * block_ms / 1000))

    stop = threading.Event()
    threading.Timer(2.0, stop.set).start()

    started = time.perf_counter()
    audio = recorder.MicRecorder(log=lambda _: None).record(push_to_talk=True, stop_event=stop)
    elapsed = time.perf_counter() - started
    duration = len(audio) / 16000
    print(f"писалось {duration:.1f} с (реально {elapsed:.1f} с), dtype={audio.dtype}")

    ok = 1.5 <= duration <= 3.0 and audio.dtype == np.int16
    print("push-to-talk останавливается по отпусканию:", "ok" if ok else "ОШИБКА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
