"""Проверка VAD без микрофона: подменяем аудиопоток файлом с тишиной по краям."""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import recorder  # noqa: E402

SAMPLE = ROOT / "tests" / "example.wav"


def read_sample() -> np.ndarray:
    with wave.open(str(SAMPLE), "rb") as wf:
        assert wf.getframerate() == 16_000
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768


class FakeStream:
    def __init__(self, audio: np.ndarray):
        self.audio = audio
        self.pos = 0

    def start(self):
        pass

    def read(self, frames):
        chunk = self.audio[self.pos:self.pos + frames]
        self.pos += frames
        if len(chunk) < frames:
            chunk = np.pad(chunk, (0, frames - len(chunk)))
        return chunk.reshape(-1, 1), False

    def stop(self):
        pass

    def close(self):
        pass


def run(audio: np.ndarray):
    recorder.sd.InputStream = lambda **kwargs: FakeStream(audio)
    return recorder.MicRecorder(log=lambda _: None).record(max_seconds=25, silence_ms=700, start_timeout=5)


def main() -> int:
    speech = read_sample()
    # 0.5 с тишины -> речь -> 1.5 с тишины
    stream = np.concatenate([np.zeros(8000, np.float32), speech, np.zeros(24000, np.float32)])
    result = run(stream)
    got = len(result) / 16000
    expected = len(speech) / 16000
    print(f"речь {expected:.1f} с -> записано {got:.1f} с")
    assert got >= expected * 0.9, "речь обрезана"
    # к речи добавляются 400 мс предзаписи и 700 мс паузы до автостопа
    assert got < expected + 1.3, "VAD не остановился после паузы"

    try:
        silence = run(np.zeros(16000 * 3, np.float32))
    except recorder.RecorderError:
        print("тишина -> RecorderError (ok)")
    else:
        raise AssertionError(f"на тишине вернулось {len(silence)} сэмплов")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print("ОШИБКА:", exc)
        sys.exit(1)
    except recorder.RecorderError as exc:
        print("тишина распознана как тишина:", exc)
        sys.exit(0)
