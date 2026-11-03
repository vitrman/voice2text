"""Созвон: нарезка по паузам, порядок реплик, транскрипт, отмена и продолжение."""

from __future__ import annotations

import shutil
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import call_transcribe  # noqa: E402
import calls  # noqa: E402

RATE = 16000


def write_pattern(path: Path, pattern: list[tuple[float, float]]) -> None:
    """pattern: [(секунды, амплитуда)] — амплитуда 0 = тишина."""
    parts = []
    for seconds, amplitude in pattern:
        count = int(RATE * seconds)
        t = np.arange(count) / RATE
        parts.append((amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
    data = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    pcm = np.clip(data * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm.tobytes())


class FakeEngine:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def transcribe_file(self, path: str) -> str:
        self.paths.append(path)
        return f"текст {len(self.paths)}"


def main() -> int:
    results: list[bool] = []
    work = Path(tempfile.gettempdir()) / "call-test"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    original_root = calls.CALLS_DIR
    calls.CALLS_DIR = work
    session = calls.new_session()

    # микрофон: речь 2 с, пауза 1.5 с, речь 1 с
    write_pattern(session / calls.MIC_FILE, [(1.0, 0.0), (2.0, 0.25), (1.5, 0.0), (1.0, 0.25)])
    # система: речь 1 с сразу после старта (перекрытие с микрофоном) и 2 с позже
    write_pattern(session / calls.SYSTEM_FILE,
                  [(0.5, 0.0), (1.0, 0.2), (4.0, 0.0), (2.0, 0.2)])

    meta = {"started_local": "03.11.2026 16:00", "duration": 8.0,
            "mic_device": "Микрофон (USB)", "system_device": "Динамики (Realtek)"}
    calls.write_meta(session, meta)

    segments = call_transcribe.build_segments(session)
    print("реплики:", [(round(s["start"], 2), s["speaker"]) for s in segments])
    ok = len(segments) == 4
    results.append(ok)
    print(f"{'ok  ' if ok else 'FAIL'} нарезка дала 4 реплики")
    order_ok = all(segments[i]["start"] <= segments[i + 1]["start"] for i in range(len(segments) - 1))
    results.append(order_ok)
    print(f"{'ok  ' if order_ok else 'FAIL'} реплики отсортированы по времени")
    speakers = {seg["speaker"] for seg in segments}
    both = speakers == {"me", "other"}
    results.append(both)
    print(f"{'ok  ' if both else 'FAIL'} есть обе дорожки: {speakers}")

    engine = FakeEngine()
    state = call_transcribe.transcribe_session(session, engine)
    transcript = (session / calls.TRANSCRIPT_FILE).read_text(encoding="utf-8")
    print("транскрипт:")
    for line in transcript.splitlines():
        print("   ", line)
    text_ok = "Я:" in transcript and "Собеседник:" in transcript and "[00:" in transcript
    results.append(text_ok)
    print(f"{'ok  ' if text_ok else 'FAIL'} метки и время в транскрипте")
    status_ok = state["status"] == "done" and len(state["segments"]) == 4
    results.append(status_ok)
    print(f"{'ok  ' if status_ok else 'FAIL'} состояние done, реплик 4")

    # повторный запуск не распознаёт заново
    before = len(engine.paths)
    call_transcribe.transcribe_session(session, engine)
    repeat_ok = len(engine.paths) == before
    results.append(repeat_ok)
    print(f"{'ok  ' if repeat_ok else 'FAIL'} готовый созвон не распознаётся повторно")

    pending_ok = not calls.pending_sessions()
    results.append(pending_ok)
    print(f"{'ok  ' if pending_ok else 'FAIL'} завершённый созвон не висит в незавершённых")

    # отмена: новая сессия, отменяем после первой реплики
    session2 = calls.new_session()
    write_pattern(session2 / calls.MIC_FILE, [(0.3, 0.0), (2.0, 0.25)])
    write_pattern(session2 / calls.SYSTEM_FILE, [(0.3, 0.0), (2.0, 0.2)])
    calls.write_meta(session2, {"started_local": "03.11.2026 17:00", "duration": 2.3})
    cancel = __import__("threading").Event()
    engine2 = FakeEngine()

    def progress(done: int, total: int) -> None:
        if done >= 1:
            cancel.set()

    try:
        call_transcribe.transcribe_session(session2, engine2, cancel=cancel, progress=progress)
    except call_transcribe.CallCancelled:
        cancel_ok = True
    else:
        cancel_ok = False
    results.append(cancel_ok)
    print(f"{'ok  ' if cancel_ok else 'FAIL'} отмена прерывает расшифровку")

    partial = calls.read_state(session2)
    resumed_ok = partial.get("status") == "recorded" and bool(partial.get("segments"))
    results.append(resumed_ok)
    print(f"{'ok  ' if resumed_ok else 'FAIL'} после отмены состояние recorded с частью текста")

    calls.write_meta(session2, {"started_local": "03.11.2026 17:00", "duration": 2.3})
    pending_ok = len(calls.pending_sessions()) == 1
    results.append(pending_ok)
    print(f"{'ok  ' if pending_ok else 'FAIL'} недоделанный созвон виден как незавершённый")

    calls.CALLS_DIR = original_root
    shutil.rmtree(work, ignore_errors=True)

    ok = all(results)
    print("результат:", "ok" if ok else "ОШИБКА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
