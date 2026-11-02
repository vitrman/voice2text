"""Проверка открытия микрофона так, как это делает приложение (16 кГц, 50 мс)."""

from __future__ import annotations

import sys
import threading
import time

import numpy as np
import sounddevice as sd

RESULT = {"text": "не завершилось", "done": False}


def probe(latency, block_ms: int, rate: int | None = 16000) -> None:
    kwargs = {"channels": 1, "dtype": "float32"}
    if latency is not None:
        kwargs["latency"] = latency
    if rate:
        kwargs["samplerate"] = rate
    if rate:
        kwargs["blocksize"] = int(rate * block_ms / 1000)
    started = time.perf_counter()
    stream = sd.InputStream(**kwargs)
    stream.start()
    opened = time.perf_counter() - started
    levels = []
    for _ in range(10):
        block, overflowed = stream.read(kwargs.get("blocksize") or 1024)
        mono = block[:, 0] if block.ndim > 1 else block
        levels.append(20 * np.log10(float(np.sqrt(np.mean(mono ** 2))) + 1e-9))
    stream.stop()
    stream.close()
    RESULT["text"] = (f"latency={latency} block={block_ms} rate={rate}: open {opened:.2f} с, "
                      f"уровни {min(levels):.0f}..{max(levels):.0f} дБ")


def run(**kwargs) -> None:
    RESULT["done"] = False
    worker = threading.Thread(target=lambda: (probe(**kwargs), RESULT.update(done=True)),
                              daemon=True)
    worker.start()
    worker.join(8.0)
    print(RESULT["text"] if RESULT["done"] else f"ЗАВИСЛО: {kwargs}")


def main() -> int:
    print("default input:", sd.query_devices(kind="input")["name"],
          int(sd.query_devices(kind="input")["default_samplerate"]), "Гц")
    run(latency="high", block_ms=50)
    run(latency=None, block_ms=50)
    run(latency="high", block_ms=30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
