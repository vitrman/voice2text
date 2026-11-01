"""Проверка микрофона: живой уровень и понятный вердикт.

Нужна, чтобы «речь не обнаружена» не оставалось загадкой: видно, слышит ли
микрофон, тихо ли говорит человек и не занято ли устройство другой программой.
"""

from __future__ import annotations

import threading
import time

import customtkinter as ctk
import numpy as np
import sounddevice as sd

from recorder import TARGET_SR, device_index
from theme import BORDER, CARD, CARD_HOVER, ERROR, MUTED, OK, TEXT, WARN

BLOCK = 0.05


def verdict_for(peak_db: float) -> tuple[str, str]:
    """Текст вердикта и цвет по пиковому уровню (дБFS)."""
    if peak_db <= -100:
        return "Нет сигнала. Проверьте, что микрофон подключён и не занят другой программой.", ERROR
    if peak_db < -55:
        return "Слишком тихо. Говорите ближе к микрофону или поднимите его громкость.", WARN
    if peak_db < -40:
        return "Тиховато — но распознавать можно.", WARN
    if peak_db > -6:
        return "Слишком громко: возможны искажения, отодвиньтесь от микрофона.", WARN
    return "Слышно хорошо.", OK


class MicChecker:
    """Читает микрофон и запоминает уровень; файлов не создаёт."""

    def __init__(self, device=None, log=print):
        self.device = device
        self.log = log
        self.last_db = -120.0
        self.peak_db = -120.0
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread = None

    def start(self) -> None:
        self._stop.clear()
        self.error = None
        self.last_db = -120.0
        self.peak_db = -120.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        index, _found = (device_index(self.device, self.log)
                         if self.device is not None else (None, True))
        try:
            stream = sd.InputStream(samplerate=TARGET_SR, channels=1, dtype="float32",
                                    device=index, latency="high",
                                    blocksize=int(TARGET_SR * BLOCK))
            stream.start()
        except Exception as exc:
            self.error = str(exc)
            return
        try:
            while not self._stop.is_set():
                block, _overflowed = stream.read(int(TARGET_SR * BLOCK))
                mono = block[:, 0] if block.ndim > 1 else block
                rms = float(np.sqrt(np.mean(np.asarray(mono, dtype=np.float64) ** 2)) + 1e-9)
                db = 20.0 * np.log10(rms)
                self.last_db = db
                self.peak_db = max(self.peak_db, db)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


class MicCheckFrame(ctk.CTkFrame):
    """Шкала уровня и вердикт; встраивается и в настройки, и в визард."""

    def __init__(self, master, device=None, log=print, seconds: float = 6.0, **kwargs):
        super().__init__(master, fg_color=CARD, corner_radius=12, **kwargs)
        self.device = device
        self.log = log
        self.seconds = seconds
        self.checker: MicChecker | None = None
        self._after = None
        self._started_at = 0.0

        self.bar = ctk.CTkProgressBar(self, height=12, corner_radius=6,
                                      progress_color=OK, fg_color=CARD_HOVER)
        self.bar.set(0)
        self.bar.pack(fill="x", padx=16, pady=(16, 8))
        self.label = ctk.CTkLabel(self, text="Нажмите «Проверить» и говорите",
                                  anchor="w", justify="left", wraplength=430,
                                  text_color=MUTED, font=ctk.CTkFont("Segoe UI", 12))
        self.label.pack(fill="x", padx=16, pady=(0, 14))

    def start(self) -> None:
        self.stop()
        self.checker = MicChecker(self.device, self.log)
        self.checker.start()
        self._started_at = time.monotonic()
        self.bar.set(0)
        self.label.configure(text="Говорите…", text_color=TEXT)
        self._poll()

    def stop(self) -> None:
        if self._after is not None:
            try:
                self.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self.checker is not None:
            self.checker.stop()
            self.checker = None
        self.bar.set(0)

    def _poll(self) -> None:
        checker = self.checker
        if checker is None:
            return
        if checker.error:
            self.label.configure(text=f"Не удалось открыть микрофон: {checker.error}",
                                 text_color=ERROR)
            self.stop()
            return
        level = max(0.0, min(100.0, (checker.last_db + 60) * 100 / 60))
        self.bar.set(level / 100)
        if self.seconds and time.monotonic() - self._started_at >= self.seconds:
            text, color = verdict_for(checker.peak_db)
            self.label.configure(text=text, text_color=color)
            self.stop()
            return
        self._after = self.after(100, self._poll)


def open_mic_test(master, device=None, log=print) -> ctk.CTkToplevel:
    """Отдельное окно проверки: 6 секунд, уровень, вердикт."""
    from theme import ACCENT, ACCENT_HOVER

    window = ctk.CTkToplevel(master)
    window.title("Проверка микрофона")
    window.geometry("470x260")
    window.resizable(False, False)
    window.configure(fg_color="#121316")
    window.attributes("-topmost", True)

    ctk.CTkLabel(window, text="Проверка микрофона", anchor="w",
                 font=ctk.CTkFont("Segoe UI", 15, "bold")).pack(fill="x", padx=18, pady=(16, 8))
    frame = MicCheckFrame(window, device=device, log=log, seconds=6.0)
    frame.pack(fill="both", expand=True, padx=18, pady=(0, 10))

    buttons = ctk.CTkFrame(window, fg_color="transparent")
    buttons.pack(fill="x", padx=18, pady=(0, 14))
    ctk.CTkButton(buttons, text="Ещё раз", width=110, height=32, corner_radius=9,
                  fg_color=ACCENT, hover_color=ACCENT_HOVER,
                  font=ctk.CTkFont("Segoe UI", 12),
                  command=frame.start).pack(side="left")
    ctk.CTkButton(buttons, text="Закрыть", width=110, height=32, corner_radius=9,
                  fg_color="transparent", border_width=1, border_color=BORDER,
                  text_color=TEXT, hover_color=CARD_HOVER,
                  font=ctk.CTkFont("Segoe UI", 12),
                  command=lambda: (frame.stop(), window.destroy())).pack(side="right")

    window.protocol("WM_DELETE_WINDOW", lambda: (frame.stop(), window.destroy()))
    frame.start()
    return window
