"""Очередь распознавания: запись не ждёт, пока модель досчитает прошлую фразу.

Модель одноядерная, поэтому распознаём строго по одному заданию — зато записывать
следующую фразу можно сразу. Заказ хранится путём к WAV: память не растёт, а при
сбое запись остаётся и её можно повторить.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path


class TranscriptionCancelled(RuntimeError):
    """Распознавание отменено пользователем."""


class TranscriptionQueue:
    """Один рабочий поток, FIFO-порядок, отмена текущего и очереди."""

    def __init__(self, transcribe, on_text, on_error=None, on_state=None, log=None):
        self._transcribe = transcribe                 # (path, cancel) -> str
        self._on_text = on_text                       # (text, task) -> None
        self._on_error = on_error or (lambda exc, task: None)
        self._on_state = on_state or (lambda depth: None)
        self._log = log or (lambda message: None)
        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._depth = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def depth(self) -> int:
        with self._lock:
            return self._depth

    def submit(self, path, target=None) -> None:
        self._queue.put({"path": Path(path), "target": target})
        with self._lock:
            self._depth += 1
            depth = self._depth
        self._on_state(depth)

    def cancel_all(self) -> int:
        """Чистит очередь и прерывает текущее распознавание. Возвращает число задач."""
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            dropped += 1
        self._cancel.set()
        with self._lock:
            self._depth = 0
        self._on_state(0)
        self._log(f"распознавание: отменено задач {dropped}")
        return dropped

    def stop(self) -> None:
        self._stop.set()
        self.cancel_all()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                self._depth = max(0, self._depth - 1)
                depth = self._depth
            self._on_state(depth)
            self._cancel.clear()
            try:
                text = self._transcribe(task["path"], self._cancel)
            except TranscriptionCancelled:
                self._log("распознавание: текущая фраза отменена")
                continue
            except Exception as exc:
                self._on_error(exc, task)
                continue
            self._on_text(text, task)
