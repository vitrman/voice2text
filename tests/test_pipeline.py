"""Сквозной тест без микрофона: файл -> MicRecorder (fake stream) -> GigaAM."""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_vad import FakeStream, read_sample  # noqa: E402

import recorder  # noqa: E402
from asr_engine import GigaAMEngine  # noqa: E402


def main() -> int:
    speech = read_sample()
    stream = np.concatenate([np.zeros(8000, np.float32), speech, np.zeros(24000, np.float32)])
    recorder.sd.InputStream = lambda **kwargs: FakeStream(stream)

    audio = recorder.MicRecorder(log=lambda _: None).record()
    print(f"записано {len(audio) / 16000:.1f} с, dtype={audio.dtype}, peak={int(np.abs(audio).max())}")
    if audio.dtype != np.int16:
        print("ОШИБКА: на выходе не int16 — WAV будет мусором")
        return 1

    text = GigaAMEngine().transcribe(audio)
    print(f"текст: {text!r}")
    return 0 if text else 1


if __name__ == "__main__":
    sys.exit(main())
