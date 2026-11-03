"""Захват системного звука (WASAPI loopback): голоса собеседников.

Микрофон слышит только вас. Чтобы получить вторую сторону созвона, нужен loopback
устройства вывода — то, что реально играет в наушниках/колонках. PortAudio такого
не умеет, поэтому используем soundcard (те же WASAPI, но с флагом loopback).
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np

try:
    import soundcard as sc
except Exception:          # нет soundcard — созвон недоступен, остальное работает
    sc = None

TARGET_SR = 16_000
BLOCK_MS = 50


class LoopbackError(RuntimeError):
    """Системный звук недоступен: нет устройства вывода или soundcard не работает."""


def available() -> bool:
    if sc is None:
        return False
    try:
        sc.default_speaker()
        return True
    except Exception:
        return False


def device_name() -> str:
    if sc is None:
        return ""
    try:
        return str(sc.default_speaker().name)
    except Exception:
        return ""


def _to_int16(audio: np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(audio, dtype=np.float32) * 32767.0, -32768, 32767).astype(np.int16)


class SystemRecorder:
    """Пишет то, что играет компьютер, в WAV 16 кГц моно."""

    def __init__(self, log=print, device: str | None = None):
        self.log = log
        self.device = device

    def record_to_file(self, path: Path, stop_event=None, on_level=None) -> Path:
        if sc is None:
            raise LoopbackError("модуль soundcard не установлен")
        path = Path(path)
        try:
            # soundcard ищет устройство по строке: объект _Speaker не подходит
            speaker_name = self.device or sc.default_speaker().name
            mic = sc.get_microphone(str(speaker_name), include_loopback=True)
        except Exception as exc:
            raise LoopbackError(f"не нашёл системный звук: {exc}") from exc
        if mic is None:
            raise LoopbackError("системный звук не найден — проверьте устройство вывода")

        frames = int(TARGET_SR * BLOCK_MS / 1000)
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = wave.open(str(path), "wb")
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(TARGET_SR)
        blocks = 0
        try:
            with mic.recorder(samplerate=TARGET_SR, channels=1, blocksize=frames) as recorder:
                self.log(f"системный звук: {mic.name}")
                while stop_event is None or not stop_event.is_set():
                    data = recorder.record(numframes=frames)
                    if data is None or len(data) == 0:
                        continue
                    mono = data.mean(axis=1) if data.ndim > 1 else data
                    writer.writeframes(_to_int16(mono).tobytes())
                    blocks += 1
                    if on_level is not None:
                        rms = float(np.sqrt(np.mean(np.asarray(mono, dtype=np.float64) ** 2))
                                    + 1e-9)
                        on_level(20.0 * np.log10(rms))
        except LoopbackError:
            raise
        except Exception as exc:
            raise LoopbackError(f"системный звук прервался: {exc}") from exc
        finally:
            writer.close()

        self.log(f"дорожка системы: {path.name}, {blocks * BLOCK_MS / 1000:.0f} с")
        return path


def probe_level(seconds: float = 1.0) -> float:
    """Быстрая проверка: какой уровень сейчас в системном звуке (дБ)."""
    if sc is None:
        raise LoopbackError("модуль soundcard не установлен")
    try:
        speaker = sc.default_speaker()
        mic = sc.get_microphone(str(speaker.name), include_loopback=True)
    except Exception as exc:
        raise LoopbackError(f"не нашёл системный звук: {exc}") from exc
    frames = int(TARGET_SR * 0.05)
    peak = -120.0
    deadline = time.monotonic() + seconds
    with mic.recorder(samplerate=TARGET_SR, channels=1, blocksize=frames) as recorder:
        while time.monotonic() < deadline:
            data = recorder.record(numframes=frames)
            if data is None or len(data) == 0:
                continue
            mono = data.mean(axis=1) if data.ndim > 1 else data
            rms = float(np.sqrt(np.mean(np.asarray(mono, dtype=np.float64) ** 2)) + 1e-9)
            peak = max(peak, 20.0 * np.log10(rms))
    return peak
