"""Пакетная расшифровка созвона: нарезка по паузам и распознавание реплик.

Нарезка делается по файлу, а не во время разговора: так проще, качественнее и
не мешает записи. Каждая реплика знает свою дорожку — отсюда метки «Я» и
«Собеседник».
"""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path

import numpy as np

import calls

FRAME_SEC = 0.03
MIN_SEGMENT_SEC = 0.4      # «да», «угу» тоже нужны
MAX_SEGMENT_SEC = 30.0
PAUSE_SEC = 0.7
NOISE_MARGIN_DB = 8.0
ABS_FLOOR_DB = -55.0


class CallCancelled(RuntimeError):
    """Расшифровка отменена пользователем (готовое сохраняется)."""


def _frame_levels(path: Path) -> tuple[np.ndarray, int, int]:
    """Уровни по кадрам (дБ) и параметры файла: (db, sample_rate, frame_size)."""
    with wave.open(str(path)) as handle:
        rate = handle.getframerate()
        frame = max(1, int(rate * FRAME_SEC))
        levels: list[float] = []
        while True:
            raw = handle.readframes(frame)
            if len(raw) < 2:
                break
            data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(data ** 2)) + 1e-9)
            levels.append(20.0 * np.log10(rms))
    return np.asarray(levels), rate, frame


def ranges_from_levels(levels: np.ndarray, frame_sec: float = FRAME_SEC) -> list[tuple[float, float]]:
    """Границы реплик по уровням: паузы < PAUSE_SEC остаются внутри реплики."""
    if len(levels) == 0:
        return []
    floor = float(np.percentile(levels, 10))
    threshold = max(floor + NOISE_MARGIN_DB, ABS_FLOOR_DB)
    voiced = levels > threshold
    pause_frames = max(1, int(PAUSE_SEC / frame_sec))
    min_frames = max(1, int(MIN_SEGMENT_SEC / frame_sec))
    max_frames = max(1, int(MAX_SEGMENT_SEC / frame_sec))

    ranges: list[tuple[int, int]] = []
    start = None
    silence = 0
    for index, flag in enumerate(voiced):
        if flag:
            if start is None:
                start = index
            silence = 0
        elif start is not None:
            silence += 1
            if silence >= pause_frames:
                ranges.append((start, index - silence + 1))
                start = None
                silence = 0
    if start is not None:
        ranges.append((start, len(voiced)))

    result: list[tuple[float, float]] = []
    for begin, end in ranges:
        while end - begin > max_frames:
            result.append((begin * frame_sec, (begin + max_frames) * frame_sec))
            begin += max_frames
        if end - begin >= min_frames:
            result.append((begin * frame_sec, end * frame_sec))
    return result


def segments_for_file(path: Path, speaker: str) -> list[dict]:
    levels, _rate, _frame = _frame_levels(path)
    return [{"start": start, "end": end, "speaker": speaker, "file": path.name}
            for start, end in ranges_from_levels(levels)]


def build_segments(session: Path) -> list[dict]:
    """Реплики обеих дорожек, отсортированные по времени начала."""
    result: list[dict] = []
    for name, speaker in ((calls.MIC_FILE, "me"), (calls.SYSTEM_FILE, "other")):
        path = session / name
        if path.exists():
            result.extend(segments_for_file(path, speaker))
    result.sort(key=lambda item: (item["start"], 0 if item["speaker"] == "me" else 1))
    for index, item in enumerate(result):
        item["index"] = index
    return result


def _segment_wav(session: Path, segment: dict) -> Path:
    """Вырезает реплику во временный WAV 16 кГц моно (для GigaAM)."""
    source = session / segment["file"]
    with wave.open(str(source)) as handle:
        rate = handle.getframerate()
        start = int(float(segment["start"]) * rate)
        end = int(float(segment["end"]) * rate)
        handle.setpos(min(start, handle.getnframes()))
        raw = handle.readframes(max(0, end - start))
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if rate != 16_000 and len(data):
        from math import gcd

        from scipy.signal import resample_poly

        divisor = gcd(int(rate), 16_000)
        data = resample_poly(data, 16_000 // divisor, rate // divisor)
    pcm = np.clip(data * 32767.0, -32768, 32767).astype(np.int16)

    handle, name = tempfile.mkstemp(suffix=".wav", dir=str(session))
    import os

    os.close(handle)
    path = Path(name)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16_000)
        out.writeframes(pcm.tobytes())
    return path


def transcribe_session(session: Path, engine, cancel=None, progress=None, log=print) -> dict:
    """Распознаёт все реплики; повторный запуск продолжает недоделанное."""
    meta = calls.read_meta(session)
    previous = {item["index"]: item.get("text", "")
                for item in calls.read_state(session).get("segments") or []
                if item.get("text")}
    segments = build_segments(session)
    total = len(segments)
    log(f"созвон: реплик {total}")
    state = {"status": "transcribing", "segments": [], "error": ""}
    calls.write_state(session, state)

    done = 0
    for segment in segments:
        if cancel is not None and cancel.is_set():
            state["status"] = "recorded"
            calls.write_state(session, state)
            calls.write_transcript(session, meta, state)
            raise CallCancelled("расшифровка отменена")

        text = previous.get(segment["index"], "")
        if not text:
            temp = None
            try:
                temp = _segment_wav(session, segment)
                text = str(engine.transcribe_file(str(temp)) or "").strip()
                text = " ".join(text.split())
            except Exception as exc:
                log(f"созвон: реплика {segment['index']} не распозналась ({exc})")
                text = ""
            finally:
                if temp is not None:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
        segment["text"] = text
        state["segments"].append(segment)
        done += 1
        calls.write_transcript(session, meta, state)
        calls.write_state(session, state)
        if progress is not None:
            progress(done, total)

    state["status"] = "done"
    calls.write_state(session, state)
    calls.write_transcript(session, meta, state)
    log(f"созвон: готово, реплик {len(state['segments'])}")
    return state
