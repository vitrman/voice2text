"""Запись с микрофона с энергетическим VAD: старт по речи, стоп по паузе."""

from __future__ import annotations

import time
from collections import deque

import numpy as np
import sounddevice as sd

TARGET_SR = 16_000

# калибровка шумовой полки: первые блоки не выбрасываются из записи
CALIBRATION_BLOCKS = 10
CALIBRATION_SPEECH_DB = 12.0


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


def input_device_items() -> list[dict]:
    """Входные устройства с именем и host API — имя переживает переподключение."""
    items: list[dict] = []
    try:
        devices = sd.query_devices()
    except Exception:
        return items
    for index, device in enumerate(devices):
        if device.get("max_input_channels", 0) > 0:
            items.append({
                "index": index,
                "name": str(device.get("name") or f"устройство {index}"),
                "hostapi": int(device.get("hostapi") or 0),
                "label": f"{index}: {device.get('name')}",
            })
    return items


def list_input_devices() -> list[str]:
    return [item["label"] for item in input_device_items()]


def device_index(spec, log=print) -> tuple[int | None, bool]:
    """Индекс устройства по сохранённому описанию: (индекс, найден).

    В конфиге хранится имя и host API, а не номер: индексы съезжают, когда
    подключают USB-гарнитуру, и запись уходила с другого микрофона.
    None в конфиге — устройство по умолчанию (найдено всегда).
    """
    if spec is None or spec == "":
        return None, True
    if isinstance(spec, int):          # старый конфиг: индекс
        return spec, True
    name = str(spec.get("name") or "").strip()
    if not name:
        return None, True
    hostapi = spec.get("hostapi")
    items = input_device_items()
    matches = [item for item in items if item["name"] == name]
    if not matches:
        matches = [item for item in items if item["name"].lower() == name.lower()]
    if not matches:
        log(f"микрофон «{name}» не найден — беру устройство по умолчанию")
        return None, False
    if hostapi is not None:
        same_api = [item for item in matches if item["hostapi"] == hostapi]
        if same_api:
            matches = same_api
    return int(matches[0]["index"]), True


class MicRecorder:
    """Пишет одну реплику. Возвращает (int16, sample_rate=16000)."""

    def __init__(self, block_ms: int = 30, log=print, device=None):
        self.block_ms = block_ms
        self.log = log
        self.device = device

    def _open_stream(self, sample_rate: int | None, device: int | None):
        if sample_rate:
            return sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32",
                                  device=device,
                                  blocksize=int(sample_rate * self.block_ms / 1000)), sample_rate
        return sd.InputStream(channels=1, dtype="float32", device=device), \
            int(sd.query_devices(kind="input")["default_samplerate"])

    def record(self, max_seconds: float = 25.0, silence_ms: int = 700, start_timeout: float = 8.0,
               on_level=None, push_to_talk: bool = False, stop_event=None) -> np.ndarray:
        """push_to_talk: пишем от нажатия до отпускания, паузы внутри не режут фразу.

        silence_ms <= 0 — автостоп по паузам выключен, пишем до Стоп/лимита.
        """
        auto_stop = silence_ms > 0
        wait_timeout = start_timeout if auto_stop else max_seconds

        index, _found = device_index(self.device, self.log) if self.device is not None else (None, True)
        try:
            stream, sr = self._open_stream(TARGET_SR, index)
            stream.start()
        except Exception:
            try:
                stream, sr = self._open_stream(None, index)
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
        peak_db = -120.0
        started_at = time.monotonic()
        blocks = 0

        try:
            while blocks < max_blocks:
                block, overflowed = stream.read(frames_per_block)
                blocks += 1
                mono = block[:, 0] if block.ndim > 1 else block
                rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)) + 1e-9)
                db = 20.0 * np.log10(rms)
                peak_db = max(peak_db, db)

                if noise_db is None:
                    # первые 300 мс: полка по минимуму, а блоки идут в запись —
                    # иначе отрезалось начало фразы, если говорить сразу после нажатия
                    calibration.append(db)
                    if push_to_talk:
                        collected.append(mono.copy())
                    else:
                        pre_roll.append(mono.copy())
                    if on_level is not None:
                        on_level(db, False)
                    if len(calibration) >= CALIBRATION_BLOCKS:
                        noise_db = float(min(calibration))
                        # в калибровке уже была речь — считаем, что она началась
                        if max(calibration) - noise_db > CALIBRATION_SPEECH_DB:
                            speaking = True
                            if not push_to_talk:
                                collected.extend(pre_roll)
                                pre_roll.clear()
                    if stop_event is not None and stop_event.is_set():
                        break
                    continue

                threshold = max(noise_db + 9.0, -55.0)
                voiced = db > threshold
                if on_level is not None:
                    on_level(db, voiced)

                if not speaking and db < noise_db:
                    # вниз подстраиваемся быстро, вверх — не ползём
                    noise_db = 0.7 * noise_db + 0.3 * db

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
                    elif time.monotonic() - started_at > wait_timeout:
                        if peak_db > noise_db + 6.0:
                            # уровень был выше шумовой полки: речь скорее всего была,
                            # поэтому не обвиняем микрофон, а отдаём запись в распознавание
                            speaking = True
                            collected.extend(pre_roll)
                            pre_roll.clear()
                            unvoiced_run = 0
                        else:
                            raise RecorderError("Речь не обнаружена — проверьте микрофон")
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

        if push_to_talk and collected:
            speaking = True      # короткое нажатие: не успели выйти из калибровки
        if not speaking:
            raise RecorderError("Речь не обнаружена")
        if push_to_talk and len(collected) * self.block_ms < 300:
            raise RecorderError("Слишком коротко — держите клавиши, пока говорите")

        audio = _resample(np.concatenate(collected), sr)
        audio = _to_int16(audio)
        self.log(f"записано {len(audio) / TARGET_SR:.1f} с, пик {int(np.abs(audio).max())}")
        return audio
