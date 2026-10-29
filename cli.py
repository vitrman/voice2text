"""Консольный режим: распознать файл или записать с микрофона.

    python cli.py --file sample.wav
    python cli.py --mic            # одна реплика: говорите, пауза 0.7 с — стоп
    python cli.py --devices        # список входов
"""

from __future__ import annotations

import argparse
import sys
import time
import wave

import numpy as np

from asr_engine import GigaAMEngine
from recorder import MicRecorder, list_input_devices


def read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as wf:
        if wf.getsampwidth() != 2:
            raise SystemExit("нужен 16-битный PCM WAV")
        channels, sr = wf.getnchannels(), wf.getframerate()
        raw = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    if channels > 1:
        raw = raw.reshape(-1, channels)[:, 0]
    return raw, sr


def main() -> int:
    parser = argparse.ArgumentParser(description="GigaAM: распознавание русской речи офлайн")
    parser.add_argument("--file", help="путь к wav (16 кГц моно, 16 бит)")
    parser.add_argument("--mic", action="store_true", help="записать одну реплику с микрофона")
    parser.add_argument("--devices", action="store_true", help="показать устройства ввода")
    parser.add_argument("--model", help="имя модели GigaAM (по умолчанию v3_e2e_rnnt)")
    args = parser.parse_args()

    if args.devices:
        for line in list_input_devices():
            print(line)
        return 0
    if not args.file and not args.mic:
        parser.print_help()
        return 2

    engine = GigaAMEngine(model_name=args.model)
    started = time.perf_counter()

    if args.file:
        audio, sr = read_wav(args.file)
        if sr != 16_000:
            from scipy.signal import resample_poly

            audio = resample_poly(audio.astype(np.float32), 16_000, sr)
            audio = np.clip(audio, -32768, 32767).astype(np.int16)
        print(f"файл: {args.file}, {len(audio) / 16_000:.1f} с", file=sys.stderr)
    else:
        print("говорите…", file=sys.stderr)
        audio = MicRecorder().record()

    text = engine.transcribe(audio)
    print(text)
    print(f"[{time.perf_counter() - started:.1f} с, модель {engine.name}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
