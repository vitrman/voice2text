"""Визард первого запуска: модель, микрофон, пробная фраза.

Первый запуск раньше выглядел так: окно, строка «Загрузка модели 12%» и тишина.
Если модель не скачалась или микрофон не тот, человек оставался с непонятной
ошибкой. Визард объясняет, что происходит, и проверяет всё по шагам.
"""

from __future__ import annotations

import threading
import time

import customtkinter as ctk

import history
import model_fetch
from asr_engine import MODEL_DIR
from mic_check import MicCheckFrame
from recorder import MicRecorder, RecorderError
from theme import (ACCENT, ACCENT_HOVER, BG, BORDER, CARD, CARD_HOVER, ERROR, MUTED,
                   OK, TEXT, WARN)

MODEL_SIZE_MB = 430


class FirstRunWizard(ctk.CTkToplevel):
    def __init__(self, master, config, engine, on_done, log=print, hotkey="ctrl+shift+space"):
        super().__init__(master)
        self.config = config
        self.engine = engine
        self.on_done = on_done
        self.log = log
        self.hotkey = hotkey
        self.model_name = str(config.get("model") or "v3_e2e_rnnt")

        self.title("Первый запуск — Голосовой ввод")
        self.geometry("680x480")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._finish)

        self._cancel = threading.Event()
        self._percent = 0
        self._poll_after = None
        self._mic: MicCheckFrame | None = None
        self._recording = False
        self._stop_event = threading.Event()

        ctk.CTkLabel(self, text="Голосовой ввод", anchor="w",
                     font=ctk.CTkFont("Segoe UI", 20, "bold")).pack(
            fill="x", padx=24, pady=(20, 0))
        ctk.CTkLabel(self, text="Три шага, чтобы всё заработало", anchor="w",
                     text_color=MUTED, font=ctk.CTkFont("Segoe UI", 12)).pack(
            fill="x", padx=24, pady=(2, 10))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=24, pady=(0, 20))
        self._clear()
        self._show_model()

    # ---------- каркас ----------
    def _clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        if self._poll_after is not None:
            try:
                self.after_cancel(self._poll_after)
            except Exception:
                pass
            self._poll_after = None

    def _buttons(self, primary: str, on_primary, secondary: str | None = None,
                 on_secondary=None) -> None:
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(side="bottom", fill="x", pady=(14, 0))
        ctk.CTkButton(row, text=primary, height=38, corner_radius=10,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      font=ctk.CTkFont("Segoe UI", 13, "bold"),
                      command=on_primary).pack(side="right")
        if secondary and on_secondary:
            ctk.CTkButton(row, text=secondary, height=38, width=130, corner_radius=10,
                          fg_color="transparent", border_width=1, border_color=BORDER,
                          text_color=TEXT, hover_color=CARD_HOVER,
                          font=ctk.CTkFont("Segoe UI", 12),
                          command=on_secondary).pack(side="right", padx=(0, 10))

    def _note(self, text: str, color: str = MUTED) -> ctk.CTkLabel:
        label = ctk.CTkLabel(self.body, text=text, anchor="w", justify="left",
                             wraplength=600, text_color=color,
                             font=ctk.CTkFont("Segoe UI", 12))
        label.pack(fill="x", pady=(0, 8))
        return label

    def _model_present(self) -> bool:
        return model_fetch.find_model(self.model_name, MODEL_DIR) is not None

    # ---------- шаг 1: модель ----------
    def _show_model(self, message: str | None = None) -> None:
        self._clear()
        if self._model_present():
            self._note("Модель GigaAM уже на месте — всё работает офлайн.")
            self._buttons("Далее", lambda: self._start_engine_then(self._show_mic))
            return

        self._note(f"Для работы нужна модель GigaAM — {MODEL_SIZE_MB} МБ. "
                   f"Она скачается один раз, дальше всё офлайн: звук никуда не уходит.")
        self._note("Скачивание занимает несколько минут и зависит от скорости интернета. "
                   "Можно отменить — недокачанное продолжится с того же места.",
                   color=MUTED)
        if message:
            self._note(message, color=WARN)
        self._buttons("Скачать", self._show_download, "Выход", self._finish)

    # ---------- шаг 2: скачивание ----------
    def _show_download(self) -> None:
        self._clear()
        self._cancel.clear()
        self._percent = 0
        self._note("Скачиваю модель…")
        bar = ctk.CTkProgressBar(self.body, height=12, corner_radius=6,
                                 progress_color=ACCENT, fg_color=CARD_HOVER)
        bar.set(0)
        bar.pack(fill="x", pady=(4, 10))
        status = ctk.CTkLabel(self.body, text="0 %", anchor="w", text_color=MUTED,
                              font=ctk.CTkFont("Segoe UI", 12))
        status.pack(fill="x")
        self._buttons("Отмена", self._cancel_download)

        def progress(percent: int) -> None:
            self._percent = percent

        def worker() -> None:
            try:
                model_fetch.ensure_model(self.model_name, MODEL_DIR, progress=progress,
                                         log=self.log, cancel=self._cancel)
            except model_fetch.DownloadCancelled:
                self.after(0, lambda: self._show_model("Скачивание отменено — можно продолжить позже."))
                return
            except Exception as exc:
                self.log(f"визард: модель не скачалась: {exc}")
                self.after(0, lambda: self._show_model(f"Не удалось скачать модель: {exc}"))
                return
            self._start_engine_then(self._show_mic)

        def poll() -> None:
            if not self.winfo_exists():
                return
            bar.set(self._percent / 100)
            status.configure(text=f"{self._percent} %")
            self._poll_after = self.after(200, poll)

        threading.Thread(target=worker, daemon=True).start()
        poll()

    def _cancel_download(self) -> None:
        self._cancel.set()

    # ---------- шаг 3: микрофон ----------
    def _start_engine_then(self, next_step) -> None:
        self._clear()

        def worker() -> None:
            try:
                self.engine.load()
            except Exception as exc:
                self.log(f"визард: модель не загрузилась: {exc}")
            self.after(0, next_step)

        threading.Thread(target=worker, daemon=True).start()

    def _show_mic(self, message: str | None = None) -> None:
        self._clear()
        self._note("Проверьте микрофон: скажите что-нибудь и посмотрите на шкалу. "
                   "Через несколько секунд появится вердикт.")
        self._mic = MicCheckFrame(self.body, device=self.config.get("device"),
                                  log=self.log, seconds=6.0)
        self._mic.pack(fill="x", pady=(0, 8))
        self._mic.start()
        if message:
            self._note(message, color=WARN)
        self._buttons("Продолжить", self._show_test, "Пропустить", self._show_test)

    # ---------- шаг 4: пробная фраза ----------
    def _show_test(self) -> None:
        if self._mic is not None:
            self._mic.stop()
            self._mic = None
        self._clear()
        self._note(f"Нажмите {self.hotkey}, скажите любую фразу и отпустите клавиши. "
                   f"Текст появится ниже и вставится в активное окно.")
        self._result = ctk.CTkLabel(self.body, text="", anchor="w", justify="left",
                                    wraplength=600, text_color=OK,
                                    font=ctk.CTkFont("Segoe UI", 13))
        self._result.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(self.body, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self._test_button = ctk.CTkButton(row, text="Проверить", height=34, width=150,
                                          corner_radius=10, fg_color=CARD,
                                          hover_color=CARD_HOVER, border_width=1,
                                          border_color=BORDER, text_color=TEXT,
                                          font=ctk.CTkFont("Segoe UI", 12),
                                          command=self._try_phrase)
        self._test_button.pack(side="left")
        self._buttons("Готово", self._finish, "Пропустить", self._finish)

    def _try_phrase(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._test_button.configure(state="disabled", text="Говорите…")
        self._result.configure(text="", text_color=OK)
        self._stop_event = threading.Event()
        path = history.take_path()

        def worker() -> None:
            recorder = MicRecorder(log=self.log, device=self.config.get("device"))
            try:
                recorder.record_to_file(path, silence_ms=0, start_timeout=6.0,
                                        stop_event=self._stop_event, limit_seconds=6.0)
                text = self.engine.transcribe_file(path)
            except RecorderError as exc:
                self.after(0, lambda: self._phrase_done(str(exc), error=True))
                return
            except Exception as exc:
                self.log(f"визард: пробная фраза не удалась: {exc}")
                self.after(0, lambda: self._phrase_done(f"Не удалось распознать: {exc}", error=True))
                return
            self.after(0, lambda: self._phrase_done(text or "Ничего не распознано"))

        def stopper() -> None:
            time.sleep(6.0)
            self._stop_event.set()

        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=stopper, daemon=True).start()

    def _phrase_done(self, text: str, error: bool = False) -> None:
        self._recording = False
        if not self.winfo_exists():
            return
        self._test_button.configure(state="normal", text="Проверить")
        self._result.configure(text=text, text_color=ERROR if error else OK)

    # ---------- завершение ----------
    def _finish(self) -> None:
        if self._mic is not None:
            self._mic.stop()
        self._stop_event.set()
        try:
            self.on_done()
        finally:
            self.destroy()
