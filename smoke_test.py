"""Проверка пайплайна без микрофона: скачивает пример речи GigaAM и распознаёт его."""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

from asr_engine import GigaAMEngine

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "tests" / "example.wav"
SAMPLE_URL = "https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM/example.wav"


def main() -> int:
    if not SAMPLE.exists():
        SAMPLE.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(SAMPLE_URL, SAMPLE)

    engine = GigaAMEngine(model_name="v3_e2e_rnnt")
    engine.load()
    print(f"модель: {engine.name}, загрузка {engine.load_seconds:.1f} с", file=sys.stderr)

    t0 = time.perf_counter()
    text = engine.transcribe_path(str(SAMPLE))
    print(f"инференс: {time.perf_counter() - t0:.2f} с", file=sys.stderr)
    print(text)
    return 0 if text else 1


if __name__ == "__main__":
    sys.exit(main())
