"""Проверка захвата системного звука через soundcard (WASAPI loopback)."""

from __future__ import annotations

import sys
import time

import numpy as np

import soundcard as sc


def main() -> int:
    speaker = sc.default_speaker()
    print("динамики по умолчанию:", speaker.name)

    mics = sc.all_microphones(include_loopback=True)
    loopbacks = [m for m in mics if m.isloopback]
    print("loopback-устройств:", len(loopbacks))
    for item in loopbacks[:5]:
        print("  ", item.name)
    if not loopbacks:
        print("ОШИБКА: loopback недоступен")
        return 1

    mic = sc.get_microphone(speaker.name, include_loopback=True)
    print("выбран:", mic.name, "loopback:", mic.isloopback)

    for rate, channels in ((16000, 1), (16000, 2), (48000, 2)):
        start = time.perf_counter()
        try:
            with mic.recorder(samplerate=rate, channels=channels, blocksize=1600) as rec:
                frames = int(rate * 1.0)
                data = rec.record(numframes=frames)
            elapsed = time.perf_counter() - start
            mono = data.mean(axis=1) if data.ndim > 1 else data
            rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)) + 1e-9)
            print(f"  {rate} Гц / {channels} канал(а): {data.shape}, "
                  f"{time.perf_counter() - start:.2f} с, уровень {20 * np.log10(rms):.0f} дБ")
        except Exception as exc:
            print(f"  {rate} Гц / {channels}: ошибка {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
