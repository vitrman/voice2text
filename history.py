"""История диктовок и последние записи.

history.log — текст с датой и временем (одна диктовка = одна строка), чтобы
надиктованное можно было найти блокнотом. last\\ — последние три WAV: без них
ошибка распознавания означала «говорите заново», а сравнить две модели на одной
фразе было невозможно.
"""

from __future__ import annotations

import time
from pathlib import Path

from paths import DATA

HISTORY_MAX_BYTES = 5 * 1024 * 1024
HISTORY_FILES = 3          # history.log + history.1.log + history.2.log
KEEP_TAKES = 3

LAST_DIR = DATA / "last"
HISTORY = DATA / "history.log"


def take_path() -> Path:
    """Уникальный путь для новой записи в last\\."""
    LAST_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = LAST_DIR / f"{stamp}.wav"
    counter = 1
    while candidate.exists():
        candidate = LAST_DIR / f"{stamp}-{counter}.wav"
        counter += 1
    return candidate


def rotate_takes(keep: int = KEEP_TAKES) -> list[Path]:
    """Оставляет несколько последних записей, старые удаляет."""
    if not LAST_DIR.is_dir():
        return []
    takes = sorted(LAST_DIR.glob("*.wav"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed = []
    for path in takes[keep:]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return removed


def _rotate_log() -> None:
    if not HISTORY.exists() or HISTORY.stat().st_size < HISTORY_MAX_BYTES:
        return
    oldest = HISTORY.with_name(f"history.{HISTORY_FILES - 1}.log")
    oldest.unlink(missing_ok=True)
    for index in range(HISTORY_FILES - 2, 0, -1):
        source = HISTORY.with_name(f"history.{index}.log")
        if source.exists():
            source.rename(HISTORY.with_name(f"history.{index + 1}.log"))
    HISTORY.rename(HISTORY.with_name("history.1.log"))


def append_text(text: str) -> None:
    """Пишет распознанный текст одной строкой: дата, время, табуляция, текст."""
    text = " ".join((text or "").split())
    if not text:
        return
    try:
        _rotate_log()
        with open(HISTORY, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{text}\n")
    except OSError:
        pass
