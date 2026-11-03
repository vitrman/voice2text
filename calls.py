"""Сессии созвонов: папка на созвон, метаданные, состояние расшифровки, транскрипт."""

from __future__ import annotations

import json
import time
from pathlib import Path

from paths import DATA

CALLS_DIR = DATA / "calls"
MIC_FILE = "mic.wav"
SYSTEM_FILE = "system.wav"
META_FILE = "meta.json"
STATE_FILE = "state.json"
TRANSCRIPT_FILE = "transcript.md"

SPEAKERS = {"me": "Я", "other": "Собеседник"}


def calls_dir() -> Path:
    CALLS_DIR.mkdir(parents=True, exist_ok=True)
    return CALLS_DIR


def new_session() -> Path:
    """Папка нового созвона: calls\\2026-11-03_16-00-00."""
    base = calls_dir()
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    session = base / stamp
    counter = 1
    while session.exists():
        session = base / f"{stamp}-{counter}"
        counter += 1
    session.mkdir(parents=True)
    return session


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_meta(session: Path, data: dict) -> None:
    _write_json(session / META_FILE, data)


def update_meta(session: Path, extra: dict) -> dict:
    meta = read_meta(session)
    meta.update(extra)
    write_meta(session, meta)
    return meta


def read_meta(session: Path) -> dict:
    return _read_json(session / META_FILE)


def write_state(session: Path, state: dict) -> None:
    _write_json(session / STATE_FILE, state)


def read_state(session: Path) -> dict:
    return _read_json(session / STATE_FILE)


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_transcript(meta: dict, state: dict) -> str:
    started = str(meta.get("started_local") or "")
    duration = float(meta.get("duration") or 0)
    lines = [f"# Созвон {started}".rstrip()]
    if duration:
        lines.append(f"Длительность: {format_time(duration)}")
    if meta.get("mic_device"):
        lines.append(f"Микрофон: {meta['mic_device']}")
    if meta.get("system_device"):
        lines.append(f"Системный звук: {meta['system_device']}")
    segments = [item for item in state.get("segments") or [] if item.get("text")]
    lines.append(f"Реплик: {len(segments)}")
    lines.append("")
    for item in sorted(segments, key=lambda seg: (seg.get("start", 0), seg.get("index", 0))):
        who = SPEAKERS.get(item.get("speaker"), item.get("speaker"))
        stamp = format_time(float(item.get("start") or 0))
        lines.append(f"- [{stamp}] {who}: {item['text']}")
    return "\n".join(lines).strip() + "\n"


def write_transcript(session: Path, meta: dict, state: dict) -> Path:
    path = session / TRANSCRIPT_FILE
    path.write_text(render_transcript(meta, state), encoding="utf-8")
    return path


def has_audio(session: Path) -> bool:
    return (session / MIC_FILE).exists() and (session / SYSTEM_FILE).exists()


def pending_sessions() -> list[Path]:
    """Созвоны, которые записаны, но ещё не расшифрованы (в том числе после сбоя)."""
    result: list[Path] = []
    folder = CALLS_DIR
    if not folder.is_dir():
        return result
    for session in sorted(folder.iterdir(), reverse=True):
        if not session.is_dir() or not has_audio(session):
            continue
        state = read_state(session)
        if state.get("status") == "done":
            continue
        result.append(session)
    return result
