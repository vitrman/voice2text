"""Запись с микрофона с энергетическим VAD: старт по речи, стоп по паузе."""

from __future__ import annotations

import time
from collections import deque

import numpy as np
import sounddevice as sd

TARGET_SR = 16_000


class RecorderError(RuntimeError):
    pass


def _to_int16(audio: np.ndarray) -> np.ndarray:
    """float32 в диапазоне [-1, 1] -> int16. Без этого WAV уходил мусором."""
    return np.clip(np.asarray(audio, dtype=np.float32) * 32767.0, -32768, 32767).astype(np.int16)


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int = TARGET_SR) -> np.ndarray:
    """Оставляет сигнал во float32, меняя только частоту дискретизации."""
    if src_sr == dst_sr:
        return np.asarray(audio, dtype=np.float32)
    from scipy.signal import resample_poly

    g = np.gcd(src_sr, dst_sr)
    return resample_poly(np.asarray(audio, dtype=np.float32), dst_sr // g, src_sr // g)


class MicRecorder:
    """Пишет одну реплику. Возвращает (int16, sample_rate=16000)."""

    def __init__(self, block_ms: int = 30, log=print, device=None):
        self.block_ms = block_ms
        self.log = log
        self.device = device

    def _open_stream(self, sample_rate: int | None):
        if sample_rate:
            return sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32",
                                  device=self.device,
                                  blocksize=int(sample_rate * self.block_ms / 1000)), sample_rate
        return sd.InputStream(channels=1, dtype="float32", device=self.device), \
            int(sd.query_devices(kind="input")["default_samplerate"])

    def record(self, max_seconds: float = 25.0, silence_ms: int = 700, start_timeout: float = 8.0,
               on_level=None, push_to_talk: bool = False, stop_event=None) -> np.ndarray:
        """push_to_talk: пишем от нажатия до отпускания, паузы внутри не режут фразу.

        silence_ms <= 0 — автостоп по паузам выключен, пишем до Стоп/лимита.
        """
        auto_stop = silence_ms > 0
        if not auto_stop:
            start_timeout = max_seconds   # ждать речь можно всю запись
        try:
            stream, sr = self._open_stream(TARGET_SR)
            stream.start()
        except Exception:
            try:
                stream, sr = self._open_stream(None)
                stream.start()
                self.log(f"микрофон не отдал 16 кГц, пишу в {sr} Гц и ресемплирую")
            except Exception as exc:
                raise RecorderError(f"Не удалось открыть микрофон: {exc}") from exc

        frames_per_block = max(1, int(sr * self.block_ms / 1000))
        silence_blocks = max(1, int(silence_ms / self.block_ms))
        max_blocks = int(max_seconds * 1000 / self.block_ms)

        pre_roll = deque(maxlen=max(1, int(400 / self.block_ms)))
        collected: list[np.ndarray] = []
        calibration: list[float] = []
        noise_db = None
        voiced_run = 0
        unvoiced_run = 0
        speaking = False
        started_at = time.monotonic()
        blocks = 0

        try:
            while blocks < max_blocks:
                block, overflowed = stream.read(frames_per_block)
                blocks += 1
                mono = block[:, 0] if block.ndim > 1 else block
                rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)) + 1e-9)
                db = 20.0 * np.log10(rms)

                if noise_db is None:
                    # первые 300 мс — калибровка шумовой полки
                    calibration.append(db)
                    if len(calibration) >= 10:
                        noise_db = float(np.percentile(calibration, 20))
                    else:
                        if on_level is not None:
                            on_level(db, False)
                        continue
                elif not speaking and db < noise_db:
                    # вниз подстраиваемся быстро, вверх — почти не ползём
                    noise_db = 0.6 * noise_db + 0.4 * db

                threshold = max(noise_db + 9.0, -55.0)
                voiced = db > threshold
                if on_level is not None:
                    on_level(db, voiced)

                if push_to_talk:
                    # пишем всё подряд: стоп только по отпусканию клавиш или лимиту
                    collected.append(mono.copy())
                    speaking = True
                    if stop_event is not None and stop_event.is_set():
                        break
                elif not speaking:
                    pre_roll.append(mono.copy())
                    voiced_run = voiced_run + 1 if voiced else 0
                    if voiced_run >= 2:
                        speaking = True
                        collected.extend(pre_roll)
                        pre_roll.clear()
                        unvoiced_run = 0
                    elif time.monotonic() - started_at > start_timeout:
                        raise RecorderError("Речь не обнаружена — попробуйте ближе к микрофону")
                    elif stop_event is not None and stop_event.is_set():
                        break
                else:
                    collected.append(mono.copy())
                    unvoiced_run = 0 if voiced else unvoiced_run + 1
                    if auto_stop and unvoiced_run >= silence_blocks:
                        break
                    if stop_event is not None and stop_event.is_set():
                        break
        finally:
            stream.stop()
            stream.close()

        if not speaking:
            raise RecorderError("Речь не обнаружена")
        if push_to_talk and len(collected) * self.block_ms < 300:
            raise RecorderError("Слишком коротко — держите клавиши, пока говорите")

        audio = _resample(np.concatenate(collected), sr)
        audio = _to_int16(audio)
        self.log(f"записано {len(audio) / TARGET_SR:.1f} с, пик {int(np.abs(audio).max())}")
        return audio


def list_input_devices() -> list[str]:
    result = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            result.append(f"{idx}: {dev['name']}")
    return result
