"""Длинная запись: нарезка по паузам и распознавание по кускам."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_vad import read_sample  # noqa: E402

from asr_engine import GigaAMEngine, split_audio  # noqa: E402


def main() -> int:
    speech = read_sample()
    pause = np.zeros(8000, np.float32)  # 0.5 с
    long_audio = np.concatenate([speech, pause, speech, pause, speech])
    audio = np.clip(long_audio * 32767, -32768, 32767).astype(np.int16)
    total = len(audio) / 16000

    chunks = split_audio(audio, 16000, max_seconds=20.0)
    lengths = [round(len(chunk) / 16000, 1) for chunk in chunks]
    print(f"всего {total:.1f} с -> куски {lengths}")
    assert all(length <= 20.5 for length in lengths), "кусок больше лимита модели"
    assert sum(lengths) >= total - 1.0, "потеряли часть записи"

    text = GigaAMEngine().transcribe(audio, chunk_seconds=20.0)
    print(f"текст ({len(text)} символов): {text[:120]}…")
    assert len(text) > 100, "текст подозрительно короткий"
    return 0


if __name__ == "__main__":
    sys.exit(main())
