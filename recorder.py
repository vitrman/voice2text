"""Запись с микрофона в WAV-файл с энергетическим VAD.

Пишем сразу на диск: память не растёт, ограничения длительности нет (только
страховка 30 минут от забытого хоткея), а файл сразу годится и в очередь
распознавания, и в «Повторить последнюю запись».
"""

from __future__ import annotations

import queue
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

TARGET_SR = 16_000
BLOCK_MS = 50                 # крупнее блок — реже теряется звук при занятом CPU
PREROLL_MS = 400
CALIBRATION_BLOCKS = 6        # 6 × 50 мс = 300 мс
CALIBRATION_SPEECH_DB = 12.0
LIMIT_SECONDS = 1800.0        # 30 минут: защита от забытого хоткея, не лимит диктовки
NO_SPEECH_SECONDS = 60.0      # столько ждём речь, потом честно говорим, что не слышно


class RecorderError(RuntimeError):
    pass


class RecordingStopped(RecorderError):
    """Запись остановил сам пользователь — это не ошибка и не повод для диалога."""

    def __init__(self, message: str = "Запись остановлена"):
        super().__init__(message)


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


def _write_wav(path: Path, audio_int16: np.ndarray, sample_rate: int = TARGET_SR) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())
    return path


def read_wav_int16(path: Path) -> np.ndarray:
    """Читает наш же WAV (16 кГц моно, 16 бит); для чужих файлов — soundfile."""
    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() == 2 and wf.getnchannels() == 1:
                return np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    except (OSError, wave.Error):
        pass
    import soundfile as sf

    data, _rate = sf.read(str(path), dtype="int16", always_2d=True)
    return data.mean(axis=1).astype(np.int16)


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
    """
    if spec is None or spec == "":
        return None, True
    if isinstance(spec, int):          # старый конфиг: индекс
        items = input_device_items()
        if any(item["index"] == spec for item in items):
            return spec, True
        log(f"сохранённый номер микрофона {spec} больше не существует — беру по умолчанию")
        return None, False
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


def open_input_stream(device_spec=None, block_ms: int = BLOCK_MS, log=print,
                      timeout: float = 4.0, total: float = 10.0, should_stop=None):
    """Открывает вход, перебирая варианты и не давая драйверу зависнуть.

    Открытие и запуск потока выполняются в отдельном потоке с таймаутом: на части
    драйверов `start()` не возвращает управление, и запись выглядела зависшей —
    «Слушаю…» навсегда, индикатор уровня стоит, кнопка «Стоп» не помогает.
    should_stop проверяется, пока ждём: нажатие «Стоп» во время открытия тоже работает.
    """
    index, _found = device_index(device_spec, log) if device_spec is not None else (None, True)
    base = {"channels": 1, "dtype": "float32", "device": index}
    attempts = []
    if block_ms:
        # сначала самый совместимый вариант: 16 кГц с фиксированным блоком и обычной
        # задержкой. «high» добавляем вторым — на части драйверов он и вешает start()
        attempts.append({**base, "samplerate": TARGET_SR,
                         "blocksize": int(TARGET_SR * block_ms / 1000)})
        attempts.append({**base, "samplerate": TARGET_SR, "latency": "high",
                         "blocksize": int(TARGET_SR * block_ms / 1000)})
    attempts.append({**base, "samplerate": TARGET_SR})
    attempts.append(dict(base))

    deadline = time.monotonic() + total
    last_error = None
    for number, kwargs in enumerate(attempts, 1):
        if should_stop is not None and should_stop():
            raise RecordingStopped()
        remaining = min(timeout, deadline - time.monotonic())
        if remaining <= 0:
            break
        started = time.perf_counter()
        holder: dict = {}

        def worker(params=kwargs, box=holder) -> None:
            try:
                stream = sd.InputStream(**params)
                stream.start()
                box["stream"] = stream
            except Exception as exc:      # ошибка драйвера — пробуем следующий вариант
                box["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        attempt_deadline = time.monotonic() + remaining
        while thread.is_alive() and time.monotonic() < attempt_deadline:
            thread.join(0.2)
            if should_stop is not None and should_stop():
                raise RecorderError("Запись остановлена")
        if thread.is_alive():
            log(f"микрофон: вариант {number} не ответил за {remaining:.0f} с — пробую другой")
            last_error = RecorderError("устройство не отвечает при открытии")
            continue
        if "error" in holder:
            last_error = holder["error"]
            log(f"микрофон: вариант {number} не открылся ({last_error})")
            continue
        stream = holder["stream"]
        rate = int(kwargs.get("samplerate")
                   or sd.query_devices(kind="input")["default_samplerate"])
        log(f"микрофон: открыт за {time.perf_counter() - started:.2f} с, "
            f"{rate} Гц, вариант {number}")
        return stream, rate
    raise RecorderError(f"Не удалось открыть микрофон: {last_error}")


def _shutdown_stream(stream, reader, reader_stop, timeout: float = 2.0, log=print) -> None:
    """Останавливает поток так, чтобы не дёргать handle, пока насос читает.

    PortAudio нельзя закрывать, пока другой поток находится внутри Pa_ReadStream:
    это гонка, которая валит процесс без записи в журнал (наблюдали «запись
    зависла, потом приложение закрылось»). Сначала abort() — он будит заблокированный
    read(), ждём выхода насоса и только потом close().
    """
    reader_stop.set()
    abort = getattr(stream, "abort", None)
    if callable(abort):
        try:
            abort()
        except Exception:
            pass
    else:
        try:
            stream.stop()
        except Exception:
            pass
    reader.join(timeout)
    if reader.is_alive():
        log("микрофон: поток чтения не завершился, поток оставляю открытым")
        return
    try:
        stream.close()
    except Exception:
        pass


class MicRecorder:
    """Пишет одну реплику в WAV. Ограничения длины нет, кроме страховки 30 минут."""

    def __init__(self, block_ms: int = BLOCK_MS, log=print, device=None):
        self.block_ms = block_ms
        self.log = log
        self.device = device
        self.last_overflow_ms = 0     # сколько звука потеряно из-за переполнения буфера
        self.read_timeout = 3.0       # столько ждём данные, прежде чем считать устройство мёртвым
        self.block_timeout = 0.2      # шаг проверки стопа, пока данных нет
        self.open_timeout = 4.0       # столько ждём открытия потока на один вариант настроек

    def record_to_file(self, path: Path, silence_ms: int = 700, start_timeout: float = 8.0,
                       on_level=None, push_to_talk: bool = False, stop_event=None,
                       limit_seconds: float = LIMIT_SECONDS) -> Path:
        """Пишет реплику в WAV. silence_ms <= 0 — автостоп по паузе выключен."""
        path = Path(path)
        auto_stop = silence_ms > 0
        wait_timeout = min(start_timeout, NO_SPEECH_SECONDS) if auto_stop else NO_SPEECH_SECONDS
        limit_blocks = int(limit_seconds * 1000 / self.block_ms)

        stream, sr = open_input_stream(
            self.device, self.block_ms, self.log, timeout=self.open_timeout,
            should_stop=(lambda: stop_event is not None and stop_event.is_set()))

        if sr != TARGET_SR:
            # редкий случай: поток не отдаёт 16 кГц. Тогда пишем в памяти и ресемплируем
            stream.stop()
            stream.close()
            self.log("устройство не поддерживает 16 кГц — пишу с ресемплированием")
            audio = self._record_memory(sr=sr, device_spec=self.device, silence_ms=silence_ms,
                                        start_timeout=start_timeout, on_level=on_level,
                                        push_to_talk=push_to_talk, stop_event=stop_event,
                                        limit_seconds=limit_seconds)
            return _write_wav(path, audio)

        frames_per_block = max(1, int(sr * self.block_ms / 1000))
        silence_blocks = max(1, int(silence_ms / self.block_ms))
        pre_roll_len = max(1, int(PREROLL_MS / self.block_ms))

        pre_roll: list[np.ndarray] = []
        calibration: list[float] = []
        noise_db = None
        voiced_run = 0
        unvoiced_run = 0
        speaking = False
        peak_db = -120.0
        started_at = time.monotonic()
        blocks = 0
        lost_blocks = 0
        limited = False

        # Чтение из устройства — в отдельном потоке, основной цикл берёт блоки из
        # очереди с таймаутом. Иначе зависший stream.read (устройство перестало
        # отдавать звук или выбран не тот вход) блокировал всё, и кнопка «Стоп»
        # не срабатывала: событие ставилось, но проверить его было некому.
        audio_queue: queue.Queue = queue.Queue(maxsize=40)
        reader_stop = threading.Event()

        def pump() -> None:
            while not reader_stop.is_set():
                try:
                    block, overflowed = stream.read(frames_per_block)
                except Exception as exc:          # устройство отвалилось
                    audio_queue.put(("error", exc))
                    return
                if reader_stop.is_set():
                    return
                try:
                    audio_queue.put(("data", (block, overflowed)), timeout=0.5)
                except queue.Full:
                    pass                          # не успеваем — блок теряется

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()

        path.parent.mkdir(parents=True, exist_ok=True)
        writer = wave.open(str(path), "wb")
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(TARGET_SR)
        last_data = time.monotonic()
        try:
            while blocks < limit_blocks:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    kind, payload = audio_queue.get(timeout=self.block_timeout)
                except queue.Empty:
                    if time.monotonic() - last_data > self.read_timeout:
                        raise RecorderError(
                            "Микрофон не отдаёт звук — проверьте устройство "
                            "кнопкой «Проверить микрофон»")
                    continue
                last_data = time.monotonic()
                if kind == "error":
                    raise RecorderError(f"Микрофон недоступен: {payload}")
                block, overflowed = payload
                blocks += 1
                if overflowed:
                    lost_blocks += 1
                mono = block[:, 0] if block.ndim > 1 else block
                rms = float(np.sqrt(np.mean(np.asarray(mono, dtype=np.float64) ** 2)) + 1e-9)
                db = 20.0 * np.log10(rms)
                peak_db = max(peak_db, db)

                if noise_db is None:
                    # первые 300 мс: полка по минимуму, блоки идут в запись
                    calibration.append(db)
                    if push_to_talk:
                        writer.writeframes(_to_int16(mono).tobytes())
                    else:
                        pre_roll.append(mono.copy())
                    if on_level is not None:
                        on_level(db, False)
                    if len(calibration) >= CALIBRATION_BLOCKS:
                        noise_db = float(min(calibration))
                        if max(calibration) - noise_db > CALIBRATION_SPEECH_DB:
                            speaking = True
                            if not push_to_talk:
                                for item in pre_roll:
                                    writer.writeframes(_to_int16(item).tobytes())
                                pre_roll.clear()
                    if stop_event is not None and stop_event.is_set():
                        break
                    continue

                threshold = max(noise_db + 9.0, -55.0)
                voiced = db > threshold
                if on_level is not None:
                    on_level(db, voiced)

                if not speaking and db < noise_db:
                    noise_db = 0.7 * noise_db + 0.3 * db

                if push_to_talk:
                    writer.writeframes(_to_int16(mono).tobytes())
                    speaking = True
                    if stop_event is not None and stop_event.is_set():
                        break
                    continue

                if not speaking:
                    pre_roll.append(mono.copy())
                    if len(pre_roll) > pre_roll_len:
                        pre_roll.pop(0)
                    voiced_run = voiced_run + 1 if voiced else 0
                    if voiced_run >= 2:
                        speaking = True
                        for item in pre_roll:
                            writer.writeframes(_to_int16(item).tobytes())
                        pre_roll.clear()
                        unvoiced_run = 0
                    elif time.monotonic() - started_at > wait_timeout:
                        if peak_db > noise_db + 6.0:
                            speaking = True
                            for item in pre_roll:
                                writer.writeframes(_to_int16(item).tobytes())
                            pre_roll.clear()
                            unvoiced_run = 0
                        else:
                            raise RecorderError("Микрофон не слышит речь — проверьте устройство")
                    elif stop_event is not None and stop_event.is_set():
                        break
                    continue

                writer.writeframes(_to_int16(mono).tobytes())
                unvoiced_run = 0 if voiced else unvoiced_run + 1
                if auto_stop and unvoiced_run >= silence_blocks:
                    break
                if stop_event is not None and stop_event.is_set():
                    break
            else:
                limited = True
        finally:
            _shutdown_stream(stream, reader, reader_stop, log=self.log)
            writer.close()

        self.last_overflow_ms = lost_blocks * self.block_ms
        if self.last_overflow_ms:
            self.log(f"переполнение аудиобуфера: потеряно {self.last_overflow_ms} мс")
        if limited:
            self.log(f"запись остановлена по пределу {limit_seconds / 60:.0f} мин")

        if push_to_talk:
            samples = max(0, path.stat().st_size - 44) // 2 if path.exists() else 0
            if samples < int(0.3 * TARGET_SR):
                path.unlink(missing_ok=True)
                raise RecorderError("Слишком коротко — держите клавиши, пока говорите")
        if not speaking:
            path.unlink(missing_ok=True)
            if stop_event is not None and stop_event.is_set():
                raise RecordingStopped()     # пользователь сам остановил — не ошибка
            raise RecorderError("Речь не обнаружена")
        return path

    def _record_memory(self, *, sr: int, device_spec, silence_ms: int, start_timeout: float,
                       on_level, push_to_talk: bool, stop_event, limit_seconds: float) -> np.ndarray:
        """Запасной путь для устройств без 16 кГц: копим в памяти и ресемплируем."""
        stream, real_sr = open_input_stream(device_spec, self.block_ms, self.log,
                                            timeout=self.open_timeout)
        frames_per_block = max(1, int(real_sr * self.block_ms / 1000))
        limit_blocks = int(limit_seconds * 1000 / self.block_ms)
        collected: list[np.ndarray] = []

        # как и в основном пути: чтение в отдельном потоке, чтобы зависшее устройство
        # не блокировало остановку
        audio_queue: queue.Queue = queue.Queue(maxsize=40)
        reader_stop = threading.Event()

        def pump() -> None:
            while not reader_stop.is_set():
                try:
                    block, _overflowed = stream.read(frames_per_block)
                except Exception:
                    return
                if reader_stop.is_set():
                    return
                try:
                    audio_queue.put(block, timeout=0.5)
                except queue.Full:
                    pass

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        last_data = time.monotonic()
        try:
            while len(collected) < limit_blocks:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    block = audio_queue.get(timeout=self.block_timeout)
                except queue.Empty:
                    if time.monotonic() - last_data > self.read_timeout:
                        raise RecorderError(
                            "Микрофон не отдаёт звук — проверьте устройство "
                            "кнопкой «Проверить микрофон»")
                    continue
                last_data = time.monotonic()
                mono = block[:, 0] if block.ndim > 1 else block
                collected.append(np.asarray(mono, dtype=np.float32))
                if on_level is not None:
                    on_level(0.0, True)
                if silence_ms > 0 and len(collected) * self.block_ms > silence_ms:
                    break
        finally:
            _shutdown_stream(stream, reader, reader_stop, log=self.log)
        if not collected:
            raise RecorderError("Речь не обнаружена")
        audio = _resample(np.concatenate(collected), real_sr)
        return _to_int16(audio)

    def record(self, max_seconds: float = 25.0, silence_ms: int = 700, start_timeout: float = 8.0,
               on_level=None, push_to_talk: bool = False, stop_event=None) -> np.ndarray:
        """Совместимый путь для cli и тестов: запись в память через временный файл."""
        import tempfile

        handle, name = tempfile.mkstemp(suffix=".wav")
        import os

        os.close(handle)
        path = Path(name)
        try:
            self.record_to_file(path, silence_ms=silence_ms, start_timeout=start_timeout,
                                on_level=on_level, push_to_talk=push_to_talk,
                                stop_event=stop_event, limit_seconds=max_seconds)
            return read_wav_int16(path)
        finally:
            path.unlink(missing_ok=True)
