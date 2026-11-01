"""Голосовой ввод: микрофон -> GigaAM -> текст (Tkinter, офлайн)."""

from __future__ import annotations

import ctypes
import datetime as dt
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk

import customtkinter as ctk

from asr_engine import GigaAMEngine
from clipboard import set_text as set_clipboard_text
from config import MODELS, MODEL_LABELS, Config
import history
import hotkey_capture
from hotkeys import HOTKEYS, parse_hotkey, probe_hotkey
from jobs import TranscriptionQueue
from postprocess import apply_replacements, tidy
from recorder import MicRecorder, RecorderError
import update
from version import VERSION
import win32
from win32 import (
    HOOKPROC, INPUT, KBDLLHOOKSTRUCT, MSG, POINT,
    KEYEVENTF_KEYUP, GWL_EXSTYLE, SWP_NOACTIVATE, SWP_SHOWWINDOW,
    VK_CONTROL, VK_V, WH_KEYBOARD_LL, WM_HOTKEY, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
)

from paths import DATA, ROOT

DEFAULT_REPO = "vitrman/voice2text"

# палитра тёмной темы
BG = "#121316"
CARD = "#1b1d21"
CARD_HOVER = "#23262b"
BORDER = "#2e3238"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#3d78e6"
RECORD = "#e0554f"
RECORD_HOVER = "#c84640"
TEXT = "#f2f3f5"
MUTED = "#8b9199"

def _input_devices() -> list[str]:
    try:
        from recorder import list_input_devices
        return ["По умолчанию"] + list_input_devices()
    except Exception:
        return ["По умолчанию"]


def _device_from_label(label: str):
    """«3: Микрофон (USB)» -> {'name':…, 'hostapi':…}: имя переживает переподключение."""
    if not label or label == "По умолчанию":
        return None
    try:
        from recorder import input_device_items
        for item in input_device_items():
            if item["label"] == label:
                return {"name": item["name"], "hostapi": item["hostapi"]}
    except Exception:
        pass
    return None


def _model_label(name: str) -> str:
    return MODEL_LABELS.get(name, name)


def _replacements_to_text(replacements: dict) -> str:
    return "\n".join(f"{src} = {dst}" for src, dst in replacements.items())


def _text_to_replacements(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        source, target = line.split("=", 1)
        source, target = source.strip(), target.strip()
        if source and target:
            result[source] = target
    return result


def _combo_error(combo: str) -> str | None:
    """Проверка записанной комбинации: текст ошибки или None."""
    parts = [part for part in combo.lower().split("+") if part]
    if not parts:
        return "ничего не нажато"
    if "win" in parts:
        return "Win не поддерживается"
    if len(parts) < 2:
        return "нужна комбинация: модификатор + клавиша"
    if not any(part in ("ctrl", "shift", "alt") for part in parts[:-1]):
        return "добавьте Ctrl, Shift или Alt"
    try:
        parse_hotkey(combo)
    except ValueError as exc:
        return str(exc)
    return None


def _ensure_streams() -> None:
    """В windowed-сборке sys.stdout/stderr = None, и gigaam/tqdm падают на записи."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


_ensure_streams()

LOG = DATA / ".cache" / "app.log"


def trace(message: str) -> None:
    """Пишет строку в app.log; при разрастании уводит старый файл в app.log.1."""
    try:
        if LOG.exists() and LOG.stat().st_size > 2_000_000:
            LOG.replace(LOG.with_suffix(".log.1"))
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def trace_exc(message: str) -> None:
    """Строка об ошибке вместе с трассировкой — по ней видно, где именно упало."""
    trace(f"{message}: {traceback.format_exc().rstrip()}")


USER32 = win32.user32

HOTKEY_TID = {"tid": 0}
WM_RELOAD_HOTKEYS = 0x8000 + 1
WM_RETRY_HOTKEYS = 0x8000 + 2
HOTKEY_RETRY_SECONDS = 60.0
_RETRY_STARTED = {"done": False}


def _hook_proc(n_code, w_param, l_param):
    """Только запись комбинации: остановку режима «удерживать» делает опрос клавиш.

    Хук не видит нажатий, когда активное окно запущено от администратора (UIPI),
    поэтому полагаться на него в остановке записи нельзя.
    """
    if n_code == 0 and hotkey_capture.is_active():
        vk = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
        hotkey_capture.feed_key(vk, w_param)
        return 1  # пока пишем комбинацию, клавиши не уходят в окна
    return USER32.CallNextHookEx(None, n_code, w_param, l_param)


def start_keyboard_hook() -> None:
    def ready() -> None:
        hotkey_capture.HOOK_READY["ok"] = True

    win32.start_keyboard_hook(_hook_proc, log=trace, on_ready=ready)



def apply_hotkey_specs(dictate: str, show: str) -> bool:
    """Разбирает комбинации в коды WinAPI (ошибка — запасная). False — была подмена."""
    return HOTKEYS.configure(dictate, show)


def set_hotkeys(dictate: str, show: str) -> None:
    """Обновляет комбинации и просит поток слушателя перерегистрировать их."""
    apply_hotkey_specs(dictate, show)
    if HOTKEY_TID["tid"]:
        USER32.PostThreadMessageW(HOTKEY_TID["tid"], WM_RELOAD_HOTKEYS, 0, 0)


def start_hotkey_listener(on_dictate, on_show, on_status=None) -> threading.Thread:
    """Глобальные горячие клавиши: диктовка и показ окна."""

    def register() -> None:
        for hotkey_id, name in ((1, "dictate"), (2, "show")):
            registered = None
            for combo in HOTKEYS.candidates(name):
                mods, vk = parse_hotkey(combo)
                if USER32.RegisterHotKey(None, hotkey_id, mods, vk):
                    registered = combo
                    break
                trace(f"hotkey: {combo} занят, ошибка {ctypes.windll.kernel32.GetLastError()}")
            if registered:
                HOTKEYS.set_actual(name, registered)
                trace(f"hotkey: {name} = {registered}")
            else:
                HOTKEYS.set_actual(name, "")
                trace(f"hotkey: {name} не удалось назначить ни одну комбинацию")
                if on_status:
                    on_status(name, None)
                continue
            if registered != HOTKEYS.requested(name) and on_status:
                on_status(name, registered)

    def retry_requested() -> None:
        """Пробует вернуть желаемую комбинацию: помешавшая программа могла закрыться."""
        for hotkey_id, name in ((1, "dictate"), (2, "show")):
            requested = HOTKEYS.requested(name)
            previous = HOTKEYS.actual(name)
            if previous == requested or not probe_hotkey(requested):
                continue
            mods, vk = parse_hotkey(requested)
            USER32.UnregisterHotKey(None, hotkey_id)
            if USER32.RegisterHotKey(None, hotkey_id, mods, vk):
                HOTKEYS.set_actual(name, requested)
                trace(f"hotkey: вернул {name} = {requested}")
                if on_status:
                    on_status(name, requested)
            elif previous:
                # не получилось — возвращаем то, что работало, чтобы не остаться без хоткея
                mods, vk = parse_hotkey(previous)
                if USER32.RegisterHotKey(None, hotkey_id, mods, vk):
                    HOTKEYS.set_actual(name, previous)

    def heartbeat() -> None:
        while True:
            time.sleep(HOTKEY_RETRY_SECONDS)
            tid = HOTKEY_TID.get("tid")
            if tid:
                USER32.PostThreadMessageW(tid, WM_RETRY_HOTKEYS, 0, 0)

    def run() -> None:
        HOTKEY_TID["tid"] = ctypes.windll.kernel32.GetCurrentThreadId()
        register()
        if not _RETRY_STARTED["done"]:
            _RETRY_STARTED["done"] = True
            threading.Thread(target=heartbeat, daemon=True).start()
        msg = MSG()
        while USER32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                if msg.wParam == 1:
                    trace("hotkey: диктовка")
                    on_dictate()
                elif msg.wParam == 2:
                    trace("hotkey: показать окно")
                    on_show()
            elif msg.message == WM_RELOAD_HOTKEYS:
                USER32.UnregisterHotKey(None, 1)
                USER32.UnregisterHotKey(None, 2)
                register()
            elif msg.message == WM_RETRY_HOTKEYS:
                retry_requested()
        USER32.UnregisterHotKey(None, 1)
        USER32.UnregisterHotKey(None, 2)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


class UpdateDialog(ctk.CTkToplevel):
    """Небольшое окно с прогрессом скачивания и кнопкой отмены."""

    def __init__(self, master, version: str, on_cancel) -> None:
        super().__init__(master)
        self.title("Обновление")
        self.geometry("430x180")
        self.resizable(False, False)
        self.configure(fg_color=BG)
        self.attributes("-topmost", True)
        self._on_cancel = on_cancel
        self._closed = False

        ctk.CTkLabel(self, text=f"Скачивание Voice2Text {version}", anchor="w",
                     font=ctk.CTkFont("Segoe UI", 15, "bold")).pack(
            fill="x", padx=20, pady=(18, 4))
        self.info = ctk.CTkLabel(self, text="0 %", anchor="w", text_color=MUTED,
                                 font=ctk.CTkFont("Segoe UI", 12))
        self.info.pack(fill="x", padx=20)
        self.bar = ctk.CTkProgressBar(self, height=10, corner_radius=5,
                                      progress_color=ACCENT, fg_color=CARD_HOVER)
        self.bar.set(0)
        self.bar.pack(fill="x", padx=20, pady=12)
        ctk.CTkButton(self, text="Отмена", width=120, height=32, corner_radius=9,
                      fg_color="transparent", border_width=1, border_color=BORDER,
                      text_color=TEXT, hover_color=CARD,
                      font=ctk.CTkFont("Segoe UI", 12),
                      command=self._cancel).pack(anchor="e", padx=20, pady=(0, 16))
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(50, self.lift)

    def set_progress(self, done: int, total: int) -> None:
        if self._closed:
            return
        if total:
            self.bar.set(min(1.0, done / total))
            self.info.configure(text=f"{done / 1e6:.0f} из {total / 1e6:.0f} МБ "
                                     f"({done * 100 // total} %)")
        else:
            self.info.configure(text=f"{done / 1e6:.0f} МБ")

    def _cancel(self) -> None:
        self._closed = True
        try:
            self._on_cancel()
        finally:
            self.destroy()


class App:
    def __init__(self, root: tk.Tk, config: Config | None = None):
        self.root = root
        self.config = config or Config()
        self.engine = GigaAMEngine(model_name=self.config["model"], log=self.log,
                                  on_progress=self._model_progress)
        self.recorder = MicRecorder(log=self.log, device=self.config.get("device"))
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.recording = False
        self.hwnd = None
        self.last_external = None
        self.paste_target = None
        self.stop_event = None
        self.ptt_active = False
        self.icon = None
        self.quitting = False
        self.start_hidden = False
        self.tray_ready = False
        self._last_take = None
        self.queue = TranscriptionQueue(
            transcribe=self._transcribe_file,
            on_text=self._queue_text,
            on_error=self._queue_error,
            on_state=self._queue_state,
            log=lambda message: trace(f"очередь: {message}"),
        )
        self._capture = None          # активная запись комбинации в панели
        self._apply_after = None      # отложенное применение настроек
        self._applying = False
        self._update_busy = False     # проверка/скачивание уже идёт
        self._update_cancel = None
        self._update_info = None
        self._update_dialog = None
        self._pending_model = None    # смена модели ждёт окончания распознавания
        LOG.parent.mkdir(exist_ok=True)
        LOG.write_text(f"=== старт {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===\n", encoding="utf-8")
        self._log_environment()
        threading.Thread(target=self._cleanup_cache, daemon=True).start()
        self.root.report_callback_exception = self._ui_exception
        self._build_ui()
        self.root.after(80, self._drain_events)
        threading.Thread(target=self._warm_up, daemon=True).start()
        self._build_overlay()
        try:
            apply_hotkey_specs(self.config.get("hotkey") or "",
                               self.config.get("show_hotkey") or "")
        except ValueError as exc:
            trace(f"hotkey: настройки некорректны ({exc}), оставляю умолчания")
        start_hotkey_listener(lambda: self._hotkey_event("hotkey"),
                              lambda: self._hotkey_event("show"),
                              lambda name, combo: self.events.put(("hotkey_changed", (name, combo))))
        start_keyboard_hook()
        self._setup_tray()
        self._warn_missing_device()
        if self.config.get("check_updates", True):
            self.root.after(15000, self._auto_update_check)

    def _warn_missing_device(self) -> None:
        """Микрофон мог исчезнуть: молча писать тишину хуже, чем сказать об этом."""
        spec = self.config.get("device")
        if spec is None:
            return
        try:
            from recorder import device_index
            _index, found = device_index(spec, log=lambda message: trace(f"device: {message}"))
        except Exception:
            return
        if not found:
            self.status.set("Выбранный микрофон не найден — пишу с устройства по умолчанию")

    # ---------- обновления ----------
    def _cleanup_cache(self) -> None:
        """Убирает архивы и распаковки обновлений, которые уже отработали."""
        try:
            freed = update.cleanup_cache(VERSION)
            freed += update.cleanup_install_leftovers(update.current_target())
            if freed:
                trace(f"кэш обновлений: освобождено {freed / 2 ** 20:.0f} МБ")
        except Exception:
            trace_exc("уборка кэша обновлений")

    def _auto_update_check(self) -> None:
        """Автопроверка не чаще раза в 12 часов: лишние запросы к GitHub не нужны."""
        try:
            last = float(self.config.get("last_update_check") or 0)
        except (TypeError, ValueError):
            last = 0
        if time.time() - last < 12 * 3600:
            return
        self.config["last_update_check"] = int(time.time())
        try:
            self.config.save()
        except OSError:
            pass
        self.check_updates(silent=True)

    def check_updates(self, silent: bool = False) -> None:
        if update.current_target() is None and not silent:
            self.status.set("Обновление доступно только для установленной версии")
            return
        if self._update_busy:
            if not silent:
                self.status.set("Обновление уже проверяется или скачивается")
            return
        repo = self.config.get("github_repo") or DEFAULT_REPO

        def worker() -> None:
            try:
                info = update.check(repo, VERSION)
            except update.NoReleases as exc:
                trace(f"обновления: {exc}")
                if not silent:
                    self.events.put(("status", f"Обновлений нет: {exc}"))
                return
            except Exception as exc:
                trace_exc("проверка обновлений")
                if not silent:
                    self.events.put(("status", f"Не удалось проверить обновления: {exc}"))
                return
            if info is None:
                if not silent:
                    self.events.put(("status", f"Обновлений нет: {VERSION} — последняя версия"))
                return
            trace(f"обновление доступно: {info['version']} ({info['size'] / 1e6:.0f} МБ)")
            self.events.put(("update_ready", info))

        threading.Thread(target=worker, daemon=True).start()

    def _offer_update(self, info: dict) -> None:
        size = (info.get("size") or 0) / 1e6
        ready = update.cached(info["name"], info.get("size") or 0)
        if ready is not None:
            question = (f"Версия {info['version']} уже скачана "
                        f"({ready.stat().st_size / 1e6:.0f} МБ). Установить сейчас?\n"
                        f"Приложение перезапустится.")
        else:
            question = (f"Доступна версия {info['version']} (установлена {VERSION}).\n"
                        f"Скачать {size:.0f} МБ и установить? Приложение перезапустится.")
        if info.get("page"):
            question += f"\n\n{info['page']}"
        if not messagebox.askyesno("Обновление", question, parent=self.root):
            return
        self._update_busy = True
        self._update_info = info
        self._update_cancel = threading.Event()
        if ready is not None:
            self._install_update(ready)
            return
        self._update_dialog = UpdateDialog(self.root, info["version"], self._cancel_update)
        threading.Thread(target=self._download_update, args=(info,), daemon=True).start()

    def _cancel_update(self) -> None:
        if self._update_cancel is not None:
            self._update_cancel.set()
        trace("обновление: отмена скачивания")

    def _download_update(self, info: dict) -> None:
        def progress(done: int, total: int) -> None:
            self.events.put(("update_progress", (done, total)))

        try:
            archive = update.download(info["url"], info["name"], progress,
                                      cancel=self._update_cancel,
                                      expected_size=info.get("size") or 0,
                                      expected_sha=info.get("sha256") or "")
        except update.UpdateCancelled:
            self.events.put(("update_cancelled", None))
            return
        except Exception as exc:
            trace_exc("скачивание обновления")
            self.events.put(("update_failed", str(exc)))
            return
        self.events.put(("update_downloaded", archive))

    def _install_update(self, archive) -> None:
        try:
            update.verify_archive(archive, (self._update_info or {}).get("sha256") or "")
            setup = update.apply(archive, update.current_target(), pid=os.getpid())
        except Exception as exc:
            trace_exc("установка обновления")
            self._update_busy = False
            self._close_update_dialog()
            self.status.set(f"Обновление не установлено: {exc}")
            messagebox.showerror("Обновление",
                                 f"Не удалось установить обновление:\n{exc}", parent=self.root)
            return
        trace(f"обновление запущено: {setup}, закрываю приложение")
        self._close_update_dialog()
        self.status.set("Установка обновления — приложение перезапустится")
        self.root.after(600, self._quit)

    def _close_update_dialog(self) -> None:
        if self._update_dialog is not None:
            try:
                self._update_dialog.destroy()
            except Exception:
                pass
            self._update_dialog = None

    # ---------- UI ----------
    def _build_ui(self) -> None:
        ctk.set_appearance_mode("dark")
        self.root.title(f"Голосовой ввод — GigaAM {VERSION}")
        self.root.geometry("1240x720")
        self.root.minsize(980, 560)
        self.root.configure(fg_color=BG)
        self._build_menu()

        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=20, pady=(16, 18))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(main, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 18))

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(header, text="Голосовой ввод",
                     font=ctk.CTkFont("Segoe UI", 20, "bold")).pack(side="left")
        ctk.CTkLabel(header, text=f"GigaAM · {VERSION}", text_color=MUTED,
                     font=ctk.CTkFont("Segoe UI", 12)).pack(side="left", padx=(10, 0), pady=(6, 0))
        self.btn_copy = ctk.CTkButton(header, text="Копировать", width=116, height=34,
                                      corner_radius=10, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                      font=ctk.CTkFont("Segoe UI", 13), command=self.copy_text)
        self.btn_copy.pack(side="right")
        self.btn_clear = ctk.CTkButton(header, text="Очистить", width=104, height=34,
                                       corner_radius=10, fg_color="transparent", border_width=1,
                                       border_color=BORDER, text_color=TEXT,
                                       hover_color=CARD_HOVER, font=ctk.CTkFont("Segoe UI", 13),
                                       command=self.clear_text)
        self.btn_clear.pack(side="right", padx=(0, 8))

        card = ctk.CTkFrame(left, corner_radius=16, fg_color=CARD)
        card.pack(fill="x", pady=(0, 10))

        self.btn_record = ctk.CTkButton(card, text=self._record_button_text(), height=46,
                                        corner_radius=12, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                        font=ctk.CTkFont("Segoe UI", 15, "bold"),
                                        command=self.toggle_recording)
        self.btn_record.pack(side="left", padx=(16, 12), pady=16)

        self.level = ctk.CTkProgressBar(card, width=200, height=10, corner_radius=5,
                                        progress_color=ACCENT, fg_color=CARD_HOVER)
        self.level.set(0)
        self.level.pack(side="left", padx=(0, 6))

        self.autopaste = tk.BooleanVar(value=bool(self.config["autopaste"]))
        ctk.CTkSwitch(card, text="Вставлять в активное окно", variable=self.autopaste,
                      width=44, font=ctk.CTkFont("Segoe UI", 13), progress_color=ACCENT,
                      command=self._schedule_apply).pack(side="left", padx=(14, 16))

        self.status = tk.StringVar(value="Загрузка модели…")
        ctk.CTkLabel(left, textvariable=self.status, text_color=MUTED, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12)).pack(fill="x", pady=(0, 8))

        self.text = ctk.CTkTextbox(left, wrap="word", corner_radius=14,
                                   fg_color=CARD, border_width=1, border_color=BORDER,
                                   text_color=TEXT, font=ctk.CTkFont("Segoe UI", 15))
        self.text.pack(fill="both", expand=True)

        self.settings_panel = ctk.CTkScrollableFrame(main, width=372, corner_radius=16,
                                                     fg_color=CARD)
        self.settings_panel.grid(row=0, column=1, sticky="ns")
        self._build_settings_panel(self.settings_panel)

        self.root.bind("<space>", self._on_space)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(300, self._remember_own_hwnd)

    # ---------- панель настроек ----------
    def _panel_label(self, text: str, top: int = 12) -> None:
        ctk.CTkLabel(self.settings_panel, text=text, anchor="w", text_color=TEXT,
                     font=ctk.CTkFont("Segoe UI", 12)).pack(fill="x", padx=16, pady=(top, 2))

    def _panel_hint(self, text: str, color: str = MUTED):
        widget = ctk.CTkLabel(self.settings_panel, text=text, anchor="w", justify="left",
                              wraplength=318, text_color=color,
                              font=ctk.CTkFont("Segoe UI", 11))
        widget.pack(fill="x", padx=16, pady=(0, 6))
        return widget

    def _bind_apply_on_leave(self, widget) -> None:
        if hasattr(widget, "_entry"):
            widget._entry.bind("<FocusOut>", lambda _e: self._schedule_apply())
            widget._entry.bind("<Return>", lambda _e: self._schedule_apply())
        else:
            widget.bind("<FocusOut>", lambda _e: self._schedule_apply())

    def _build_settings_panel(self, panel) -> None:
        ctk.CTkLabel(panel, text="Настройки", anchor="w",
                     font=ctk.CTkFont("Segoe UI", 16, "bold")).pack(fill="x", padx=16, pady=(16, 4))

        self._panel_label("Модель")
        self.var_model = tk.StringVar(value=_model_label(self.config["model"]))
        self._model_by_label = {_model_label(name): name for name in MODELS}
        model_box = ctk.CTkComboBox(panel, values=[_model_label(name) for name in MODELS],
                                    variable=self.var_model,
                                    state="readonly", corner_radius=10, fg_color=BG,
                                    border_color=BORDER, button_color=BORDER,
                                    button_hover_color=CARD_HOVER, dropdown_fg_color=BG,
                                    command=lambda _v: self._schedule_apply(),
                                    font=ctk.CTkFont("Segoe UI", 12))
        model_box.pack(fill="x", padx=16, pady=(0, 4))

        self._panel_label("Микрофон")
        self.var_device = tk.StringVar(value=self._device_label())
        device_box = ctk.CTkComboBox(panel, values=_input_devices(), variable=self.var_device,
                                     state="readonly", corner_radius=10, fg_color=BG,
                                     border_color=BORDER, button_color=BORDER,
                                     button_hover_color=CARD_HOVER, dropdown_fg_color=BG,
                                     command=lambda _v: self._schedule_apply(),
                                     font=ctk.CTkFont("Segoe UI", 12))
        device_box.pack(fill="x", padx=16, pady=(0, 4))
        # список устройств обновляем при открытии: воткнутая гарнитура появляется сразу
        device_box.bind("<Button-1>", lambda _e: self._refresh_devices(), add="+")
        self.device_box = device_box

        self._hotkey_entries: dict[str, object] = {}
        self.var_hotkey = tk.StringVar(value=self.config["hotkey"])
        self._hotkey_row("Хоткей диктовки", self.var_hotkey, "dictate")
        self.var_show = tk.StringVar(value=self.config["show_hotkey"])
        self._hotkey_row("Хоткей окна", self.var_show, "show")
        self.hotkey_note = self._panel_hint(
            "Ctrl/Shift/Alt + клавиша. Win и одиночные клавиши не поддерживаются.")

        self._panel_label("Режим хоткея")
        self.var_mode = tk.StringVar(value="удерживать" if self.config.get("hotkey_mode") == "hold"
                                   else "переключатель")
        ctk.CTkComboBox(panel, values=["переключатель", "удерживать"], variable=self.var_mode,
                        state="readonly", corner_radius=10, fg_color=BG, border_color=BORDER,
                        button_color=BORDER, button_hover_color=CARD_HOVER,
                        dropdown_fg_color=BG, command=lambda _v: self._schedule_apply(),
                        font=ctk.CTkFont("Segoe UI", 12)).pack(fill="x", padx=16, pady=(0, 4))

        row = ctk.CTkFrame(panel, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(10, 4))
        self.var_auto_stop = tk.BooleanVar(value=bool(self.config.get("auto_stop", True)))
        ctk.CTkCheckBox(row, text="Стоп по паузе, мс", variable=self.var_auto_stop,
                        font=ctk.CTkFont("Segoe UI", 12), checkbox_width=20, checkbox_height=20,
                        corner_radius=6, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        command=self._on_auto_stop_toggle).pack(side="left")
        self.var_silence = tk.IntVar(value=int(self.config["silence_ms"]))
        self.entry_silence = ctk.CTkEntry(row, textvariable=self.var_silence, width=70,
                                          corner_radius=10, fg_color=BG, border_color=BORDER)
        self.entry_silence.pack(side="left", padx=(10, 0))
        self._bind_apply_on_leave(self.entry_silence)

        self._panel_hint("Без автостопа пишем, пока не нажмёте «Стоп» (страховка — 30 минут).")

        self.var_minimized = tk.BooleanVar(value=bool(self.config["start_minimized"]))
        ctk.CTkCheckBox(panel, text="Запускать свёрнутым", variable=self.var_minimized,
                        font=ctk.CTkFont("Segoe UI", 12), checkbox_width=20, checkbox_height=20,
                        corner_radius=6, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        command=self._schedule_apply).pack(anchor="w", padx=16, pady=(8, 6))

        self._panel_label("Замены: как слышится = Как надо", top=6)
        self.txt_replacements = ctk.CTkTextbox(panel, height=110, corner_radius=10, fg_color=BG,
                                               border_width=1, border_color=BORDER,
                                               font=ctk.CTkFont("Segoe UI", 12))
        self.txt_replacements.pack(fill="x", padx=16, pady=(0, 16))
        self.txt_replacements.insert("1.0", _replacements_to_text(self.config.get("replacements") or {}))
        self.txt_replacements.bind("<FocusOut>", lambda _e: self._schedule_apply())

        ctk.CTkLabel(panel, text="Изменения применяются сразу", text_color=MUTED, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 11)).pack(fill="x", padx=16, pady=(0, 16))

    def _device_label(self) -> str:
        spec = self.config.get("device")
        if spec is None:
            return "По умолчанию"
        if isinstance(spec, int):          # старый конфиг: номер устройства
            for label in _input_devices():
                if label.startswith(f"{spec}:"):
                    return label
            return "По умолчанию"
        name = str((spec or {}).get("name") or "")
        try:
            from recorder import input_device_items
            for item in input_device_items():
                if item["name"] == name:
                    return item["label"]
        except Exception:
            pass
        return "По умолчанию"

    def _refresh_devices(self) -> None:
        """Воткнутая гарнитура должна появляться без перезапуска приложения."""
        labels = _input_devices()
        self.device_box.configure(values=labels)
        if self.var_device.get() not in labels:
            self.var_device.set(self._device_label())

    def _on_auto_stop_toggle(self) -> None:
        self.entry_silence.configure(state="normal" if self.var_auto_stop.get() else "disabled")
        self._schedule_apply()

    def _hotkey_row(self, title: str, var: tk.StringVar, name: str) -> None:
        self._panel_label(title)
        holder = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=(0, 4))
        entry = ctk.CTkEntry(holder, textvariable=var, corner_radius=10, fg_color=BG,
                             border_color=BORDER, font=ctk.CTkFont("Segoe UI", 12))
        entry.pack(side="left", fill="x", expand=True)
        self._hotkey_entries[name] = entry
        self._bind_apply_on_leave(entry)
        mark = ctk.CTkLabel(holder, text="", width=16, font=ctk.CTkFont("Segoe UI", 12))
        mark.pack(side="left", padx=(6, 0))
        ctk.CTkButton(holder, text="Записать", width=88, height=28, corner_radius=9,
                      fg_color=BG, hover_color=CARD_HOVER, border_width=1, border_color=BORDER,
                      text_color=TEXT, font=ctk.CTkFont("Segoe UI", 11),
                      command=lambda: self._start_capture(var, mark)).pack(side="left", padx=(6, 0))

    def _on_hotkey_changed(self, name: str, combo: str | None) -> None:
        """Говорит правду о хоткее: занятую комбинацию больше не прячем в строке статуса.

        В поле остаётся желаемая комбинация (намерение пользователя), а подсказка
        и рамка показывают, что реально работает сейчас.
        """
        requested = HOTKEYS.requested(name)
        entry = self._hotkey_entries.get(name)
        if combo is None:
            if entry is not None:
                entry.configure(border_color="#e0554f")
            self.hotkey_note.configure(text=f"Не удалось назначить {requested}",
                                       text_color="#e0554f")
            self.status.set(f"Хоткей {requested} не назначен")
        elif combo != requested:
            if entry is not None:
                entry.configure(border_color="#e0a33a")
            self.hotkey_note.configure(
                text=f"{requested} занята другой программой — сейчас работает {combo}. "
                     f"Попробую вернуть автоматически.",
                text_color="#e0a33a")
            self.status.set(f"{requested} занята — работает {combo}")
        else:
            if entry is not None:
                entry.configure(border_color=BORDER)
            self.hotkey_note.configure(text=f"Хоткей {combo} работает", text_color="#4caf68")
            self.status.set(f"Хоткей {combo} снова работает")
        self._refresh_record_button()

    # ---------- запись комбинации в панели ----------
    def _start_capture(self, var: tk.StringVar, mark) -> None:
        self._capture = {"var": var, "mark": mark, "after": None}
        mark.configure(text="…", text_color=MUTED)
        if not hotkey_capture.HOOK_READY["ok"]:
            self.hotkey_note.configure(text="Хук недоступен — запись комбинации не работает",
                                       text_color="#e0554f")
            return
        hotkey_capture.start_capture()
        self._capture_poll()
        self.root.after(8000, self._capture_timeout)

    def _capture_poll(self) -> None:
        if self._capture is None:
            return
        combo = hotkey_capture.poll_capture()
        if combo is None:
            self._capture["after"] = self.root.after(60, self._capture_poll)
            return
        mark = self._capture["mark"]
        if not combo:
            mark.configure(text="")
            self.hotkey_note.configure(text="Запись отменена", text_color=MUTED)
            self._capture = None
            return
        error = _combo_error(combo)
        if not error and not self._combo_free(combo):
            # проверяем сразу: иначе занятая комбинация всплывёт только после перезапуска
            error = "эта комбинация занята другой программой"
        if error:
            mark.configure(text="×", text_color="#e0554f")
            self.hotkey_note.configure(text=f"{error}. Старое значение оставлено.",
                                       text_color="#e0554f")
        else:
            self._capture["var"].set(combo)
            mark.configure(text="✓", text_color="#4caf68")
            self.hotkey_note.configure(text=f"Записано: {combo}", text_color="#4caf68")
            self._schedule_apply()
        self._capture = None

    def _capture_timeout(self) -> None:
        if self._capture is None:
            return
        hotkey_capture.cancel_capture()
        self._capture["mark"].configure(text="")
        self._capture = None

    @staticmethod
    def _combo_free(combo: str) -> bool:
        """Свободна ли комбинация: свои собственные считаем свободными."""
        ours = {HOTKEYS.shown("dictate"), HOTKEYS.shown("show"),
                HOTKEYS.requested("dictate"), HOTKEYS.requested("show")}
        if combo in ours:
            return True
        return probe_hotkey(combo)

    # ---------- применение настроек ----------
    def _schedule_apply(self) -> None:
        if getattr(self, "_applying", False):
            return
        if getattr(self, "_apply_after", None) is not None:
            self.root.after_cancel(self._apply_after)
        self._apply_after = self.root.after(400, self._apply_settings)

    def _apply_settings(self) -> None:
        self._apply_after = None
        dict_combo = self.var_hotkey.get().strip().lower()
        show_combo = self.var_show.get().strip().lower()
        for combo in (dict_combo, show_combo):
            try:
                parse_hotkey(combo)
            except ValueError as exc:
                self.status.set(f"Хоткей: {exc}")
                self.hotkey_note.configure(text=f"Хоткей: {exc}", text_color="#e0554f")
                return
        try:
            silence = max(200, int(self.var_silence.get()))
            device_text = self.var_device.get()
            device = _device_from_label(device_text)
        except (ValueError, tk.TclError):
            self.status.set("Проверьте числа: пауза перед остановкой")
            return

        values = {
            "model": self._model_by_label.get(self.var_model.get(), self.config["model"]),
            "device": device,
            "hotkey": dict_combo,
            "show_hotkey": show_combo,
            "hotkey_mode": "hold" if self.var_mode.get() == "удерживать" else "toggle",
            "silence_ms": silence,
            "auto_stop": bool(self.var_auto_stop.get()),
            "autopaste": bool(self.autopaste.get()),
            "start_minimized": bool(self.var_minimized.get()),
            "replacements": _text_to_replacements(self.txt_replacements.get("1.0", "end-1c")),
        }
        if all(self.config.get(key) == value for key, value in values.items()):
            return

        old_model = self.config["model"]
        old_hotkey = (self.config["hotkey"], self.config["show_hotkey"])
        self._applying = True
        try:
            self.config.update(values)
            self.config.save()
            self.recorder.device = self.config.get("device")
            if (self.config["hotkey"], self.config["show_hotkey"]) != old_hotkey:
                set_hotkeys(self.config["hotkey"], self.config["show_hotkey"])
                self._refresh_record_button()
            if self.config["model"] != old_model:
                # смена модели только в простое: иначе в памяти живут два чекпойнта
                if self._busy():
                    self._pending_model = self.config["model"]
                    self.status.set("Модель будет сменена после распознавания")
                else:
                    self._switch_model(self.config["model"])
        finally:
            self._applying = False
        if not self._pending_model:
            self.status.set("Настройки сохранены")

    def _busy(self) -> bool:
        """Занято, если идёт запись или в очереди есть нераспознанные фразы."""
        return self.recording or self.queue.depth > 0

    def _switch_model(self, name: str) -> None:
        old = self.engine
        self.engine = GigaAMEngine(model_name=name, log=self.log, on_progress=self._model_progress)
        old.dispose()
        threading.Thread(target=self._warm_up, daemon=True).start()

    def _apply_pending_model(self) -> None:
        if not self._pending_model:
            return
        name, self._pending_model = self._pending_model, None
        trace(f"смена модели на {name} после распознавания")
        self._switch_model(name)

    # ---------- трей ----------
    def _setup_tray(self) -> bool:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception as exc:  # нет pystray/Pillow — работаем обычным окном
            trace(f"tray: недоступен: {exc}")
            return False

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        # непрозрачная плашка: полупрозрачные иконки в трее выглядят пустыми
        draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(79, 140, 255, 255))
        draw.rounded_rectangle((26, 12, 38, 38), radius=6, fill=(255, 255, 255, 255))
        draw.rectangle((30, 38, 34, 46), fill=(255, 255, 255, 255))
        draw.rectangle((21, 46, 43, 51), fill=(255, 255, 255, 255))

        menu = pystray.Menu(
            pystray.MenuItem("Показать окно", lambda: self.events.put(("show", None)), default=True),
            pystray.MenuItem("Записать / Стоп", lambda: self.events.put(("toggle", None))),
            pystray.MenuItem("Спрятать в трей", lambda: self.events.put(("hide_tray", None))),
            pystray.MenuItem("Выход", lambda: self.events.put(("quit", None))),
        )
        self.icon = pystray.Icon("voice2text", image,
                                 f"Голосовой ввод — {HOTKEYS.shown('dictate')}", menu)
        threading.Thread(target=self.icon.run, kwargs={"setup": self._on_tray_ready},
                         daemon=True).start()
        return True

    def _on_tray_ready(self, icon) -> None:
        """Вызывается pystray, когда иконка реально создана (иначе окно остаётся видимым)."""
        trace(f"tray: иконка создана, visible={getattr(icon, 'visible', None)}")
        self.events.put(("tray_ready", None))

    # ---------- индикатор внизу экрана ----------
    def _build_overlay(self) -> None:
        self.overlay = tk.Toplevel(self.root)
        self.overlay.withdraw()
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg=CARD)

        frame = tk.Frame(self.overlay, bg=CARD, highlightthickness=1,
                         highlightbackground=BORDER)
        frame.pack(fill="both", expand=True)
        self.overlay_dot = tk.Label(frame, text="●", font=("Segoe UI", 17), bg=CARD, fg=RECORD)
        self.overlay_dot.pack(side="left", padx=(16, 8), pady=10)
        self.overlay_time = tk.Label(frame, text="", font=("Segoe UI", 13, "bold"),
                                     bg=CARD, fg=TEXT)
        self.overlay_time.pack(side="left", padx=(0, 8))
        self.overlay_text = tk.Label(frame, text="", font=("Segoe UI", 12), bg=CARD, fg=TEXT)
        self.overlay_text.pack(side="left", padx=(0, 12))
        # уровень громкости: видно, что микрофон действительно слышит
        self.overlay_canvas = tk.Canvas(frame, width=90, height=8, bg=CARD_HOVER,
                                        highlightthickness=0)
        self.overlay_canvas.pack(side="left", padx=(0, 16))
        self.overlay_bar = self.overlay_canvas.create_rectangle(0, 0, 0, 8, fill=ACCENT, width=0)

        self.overlay_after = None
        self._overlay_phase = None        # None — обычное сообщение с TTL
        self._overlay_started = 0.0
        self._overlay_dots = 0
        for widget in (self.overlay, frame, self.overlay_text, self.overlay_dot,
                       self.overlay_time, self.overlay_canvas):
            widget.bind("<Button-1>", lambda _e: self._show_window())

        self.overlay.update_idletasks()
        hwnd = USER32.GetAncestor(self.overlay.winfo_id(), 2) or self.overlay.winfo_id()
        win32.make_overlay(hwnd)
        self.overlay_hwnd = hwnd

    def _overlay_place(self) -> None:
        self.overlay.update_idletasks()
        width = max(self.overlay.winfo_reqwidth(), 260)
        height = self.overlay.winfo_reqheight()
        x = (self.overlay.winfo_screenwidth() - width) // 2
        y = self.overlay.winfo_screenheight() - height - 96
        # geometry у overrideredirect-окна применяется ненадёжно — ставим через WinAPI
        win32.place_window(self.overlay_hwnd, x, y, width, height)

    def _overlay_show(self, text: str, color: str = "#e05555", ttl_ms: int = 1500) -> None:
        """Короткое сообщение: «Вставлено», «Скопировано в буфер», ошибка."""
        if self.overlay is None:
            return
        self._overlay_phase = None
        self.overlay_time.configure(text="")
        self.overlay_text.configure(text=text)
        self.overlay_dot.configure(fg=color)
        self._overlay_place()
        if self.overlay_after is not None:
            self.root.after_cancel(self.overlay_after)
        self.overlay_after = self.root.after(ttl_ms, self._overlay_hide)

    def _overlay_recording(self) -> None:
        """Живой индикатор записи: таймер, фаза и уровень, без исчезновения по TTL."""
        if self.overlay is None:
            return
        self._overlay_phase = "recording"
        self._overlay_started = time.monotonic()
        self.overlay_dot.configure(fg=RECORD)
        self.overlay_time.configure(text="0:00")
        self.overlay_text.configure(text="говорите…")
        self._overlay_level(0)
        self._overlay_place()
        if self.overlay_after is not None:
            self.root.after_cancel(self.overlay_after)
            self.overlay_after = None
        self._overlay_tick()

    def _overlay_processing(self, depth: int = 0) -> None:
        """Фаза распознавания: видно, что запись принята и сколько ещё в очереди."""
        if self.overlay is None:
            return
        self._overlay_phase = "processing"
        self.overlay_dot.configure(fg="#e0a33a")
        self.overlay_time.configure(text="")
        self.overlay_dots = 0
        tail = f" (в очереди {depth})" if depth > 0 else ""
        self.overlay_text.configure(text=f"Распознаю{tail}")
        self._overlay_level(0)
        self._overlay_place()
        self._overlay_tick()

    def _overlay_tick(self) -> None:
        if self._overlay_phase is None:
            return
        if self._overlay_phase == "recording":
            seconds = int(time.monotonic() - self._overlay_started)
            self.overlay_time.configure(text=f"{seconds // 60}:{seconds % 60:02d}")
        else:
            self._overlay_dots = (self._overlay_dots + 1) % 4
            tail = "" if self._overlay_dots == 0 else "." * self._overlay_dots
            base = "Распознаю"
            self.overlay_text.configure(text=f"{base}{tail}")
        self.overlay_after = self.root.after(250, self._overlay_tick)

    def _overlay_level(self, percent: float) -> None:
        if self.overlay is None:
            return
        width = int(90 * max(0.0, min(100.0, percent)) / 100)
        self.overlay_canvas.coords(self.overlay_bar, 0, 0, width, 8)

    def _overlay_stop(self) -> None:
        """Снимает живую фазу; сообщения с TTL продолжают работать как раньше."""
        self._overlay_phase = None
        if self.overlay_after is not None:
            self.root.after_cancel(self.overlay_after)
            self.overlay_after = None
        self._overlay_level(0)

    def _overlay_hide(self) -> None:
        self.overlay_after = None
        if self.overlay is not None:
            self.overlay.withdraw()

    def hide_to_tray(self) -> None:
        # сворачиваем на панель задач, а не прячем полностью: иконка в трее
        # у Windows 11 часто уезжает в переполнение «^», и окно было бы не вернуть
        self.root.iconify()
        trace("окно свёрнуто на панель задач")
        self.events.put(("overlay", (f"Свёрнуто · {HOTKEYS.shown('show')} — открыть",
                                     "#4caf50", 4000)))

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit(self) -> None:
        self.quitting = True
        try:
            self.queue.stop()
        except Exception:
            pass
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
        self.root.destroy()

    def _ui_exception(self, exc, value, tb) -> None:
        trace("TK ERROR: " + "".join(traceback.format_exception(exc, value, tb)))
        self.status.set(f"Ошибка интерфейса: {value}")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Повторить последнюю запись", command=self.retry_last)
        file_menu.add_command(label="Отменить распознавание", command=self.cancel_recognition)
        file_menu.add_separator()
        file_menu.add_command(label="Свернуть", command=self.hide_to_tray)
        file_menu.add_command(label="Выход", command=self._quit)
        menubar.add_cascade(label="Файл", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Проверить обновления…", command=self.check_updates)
        help_menu.add_separator()
        help_menu.add_command(label="Диагностика", command=self.run_diagnostics)
        help_menu.add_command(label="Открыть журнал", command=self.open_log)
        help_menu.add_command(label="Открыть историю", command=self.open_history)
        help_menu.add_command(label="Открыть папку данных", command=self.open_data_folder)
        help_menu.add_command(label="О программе", command=self.show_about)
        menubar.add_cascade(label="Справка", menu=help_menu)
        self.root.config(menu=menubar)

    def open_history(self) -> None:
        try:
            history.HISTORY.parent.mkdir(parents=True, exist_ok=True)
            history.HISTORY.touch(exist_ok=True)
            os.startfile(str(history.HISTORY))  # noqa: S606
        except OSError as exc:
            self.status.set(f"Не удалось открыть историю: {exc}")

    def open_log(self) -> None:
        try:
            os.startfile(str(LOG))  # noqa: S606 — Windows открывает файл своим просмотрщиком
        except OSError as exc:
            self.status.set(f"Не удалось открыть журнал: {exc}")

    def open_data_folder(self) -> None:
        try:
            os.startfile(str(DATA))  # noqa: S606
        except OSError as exc:
            self.status.set(f"Не удалось открыть папку: {exc}")

    def _log_environment(self) -> None:
        """Паспорт машины в журнале — без него чужие логи бесполезны."""
        import platform
        import shutil

        from asr_engine import MODEL_DIR

        try:
            trace(f"версия {VERSION}; frozen={getattr(sys, 'frozen', False)}; exe={sys.executable}")
            trace(f"python {platform.python_version()}; {platform.platform()}")
            trace(f"cwd={os.getcwd()}; ROOT={ROOT}; DATA={DATA}")
            trace(f"ffmpeg={'есть' if shutil.which('ffmpeg') else 'нет'} (не требуется)")
            trace(f"админ={bool(ctypes.windll.shell32.IsUserAnAdmin())}")
            trace(f"свободно {shutil.disk_usage(str(DATA)).free / 2**30:.1f} ГБ в {DATA}")
            trace(f"модель {self.engine.name}; каталог {MODEL_DIR}")
            try:
                from recorder import list_input_devices
                devices = list_input_devices()
                trace(f"микрофоны ({len(devices)}): {'; '.join(devices) or 'нет'}")
            except Exception as exc:
                trace(f"список микрофонов недоступен: {exc!r}")
        except Exception:
            trace_exc("не удалось записать паспорт окружения")

    def run_diagnostics(self) -> None:
        log_tail = ""
        try:
            log_tail = "\n".join(LOG.read_text(encoding="utf-8").splitlines()[-25:])
        except OSError:
            pass
        report = (
            f"Версия: {VERSION}\n"
            f"Python: {sys.version.split()[0]}\n"
            f"Модель: {self.engine.name}\n"
            f"Каталог: {ROOT}\n"
            f"Лог: {LOG}\n"
            f"Микрофон: {self.config.get('device') or 'по умолчанию'}\n"
            f"Хоткеи: {self.config['hotkey']} / {self.config['show_hotkey']}\n\n"
            f"--- последние строки лога ---\n{log_tail}"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(report)
        messagebox.showinfo("Диагностика",
                            f"Отчёт скопирован в буфер обмена.\nЛоги: {LOG.parent}",
                            parent=self.root)

    def show_about(self) -> None:
        messagebox.showinfo(
            "О программе",
            f"Голосовой ввод {VERSION}\n"
            f"GigaAM (Сбер), офлайн, русский язык\n"
            f"Модель: {self.engine.name}\n\n"
            f"Диктовка: {HOTKEYS.shown('dictate')}\n"
            f"Показать окно: {HOTKEYS.shown('show')}",
            parent=self.root)

    def _remember_own_hwnd(self) -> None:
        # wm_frame() — гарантированно верхнее окно Tk; GetAncestor может вернуть дочернее,
        # из-за чего приложение принимало собственное окно за чужое и вставляло текст себе
        try:
            frame = self.root.wm_frame()
            self.hwnd = int(frame, 16) if frame else 0
        except (ValueError, tk.TclError):
            self.hwnd = 0
        if not self.hwnd:
            self.hwnd = USER32.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
        trace(f"own hwnd={self.hwnd}")
        self.root.after(400, self._track_foreground)

    def _is_own(self, hwnd: int | None) -> bool:
        """Окно принадлежит нашему процессу (включая рамку Tk и оверлей)."""
        if not hwnd:
            return False
        if hwnd in (self.hwnd, getattr(self, "overlay_hwnd", None)):
            return True
        pid = ctypes.c_ulong()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return bool(pid.value) and pid.value == os.getpid()

    def _track_foreground(self) -> None:
        """Запоминаем последнее чужое окно — туда и будем вставлять текст."""
        hwnd = USER32.GetForegroundWindow()
        if hwnd and not self._is_own(hwnd):
            self.last_external = hwnd
        self.root.after(400, self._track_foreground)

    def _on_space(self, event):
        if isinstance(event.widget, (tk.Text, tk.Entry, ctk.CTkTextbox, ctk.CTkEntry)):
            return None
        self.toggle_recording()
        return "break"

    # ---------- фон ----------
    def _warm_up(self) -> None:
        try:
            self.engine.load()
            self.events.put(("status", f"Готово. Модель: {self.engine.name}. Нажмите «Записать» и говорите."))
        except Exception as exc:
            self.events.put(("status", f"Модель не загрузилась: {exc}"))

    def _record_button_text(self) -> str:
        """Надпись кнопки показывает фактический хоткей, а не зашитый в код."""
        return f"● Записать ({HOTKEYS.shown('dictate')})"

    def _stop_button_text(self) -> str:
        return f"■ Стоп ({HOTKEYS.shown('dictate')})"

    def _refresh_record_button(self) -> None:
        self._set_record_state(self.recording)

    def _set_record_state(self, recording: bool) -> None:
        self.btn_record.configure(
            text=self._stop_button_text() if recording else self._record_button_text(),
            fg_color=RECORD if recording else ACCENT,
            hover_color=RECORD_HOVER if recording else ACCENT_HOVER)

    def toggle_recording(self) -> None:
        if self.recording and self.stop_event is not None:
            # кнопка «Стоп» на форме останавливает и рацию, и режим переключателя
            trace("кнопка: стоп")
            self.stop_event.set()
            return
        self._start_recording(push_to_talk=False, source="кнопка")

    def _remember_target(self) -> None:
        # цель вставки — окно, активное в момент старта записи
        foreground = USER32.GetForegroundWindow()
        if foreground and not self._is_own(foreground):
            self.paste_target = foreground
        else:
            # фокус в нашем окне: берём последнее чужое, иначе вставлять некуда
            self.paste_target = self.last_external
        trace(f"paste target set: {self.paste_target} "
              f"(foreground={foreground} own={self._is_own(foreground)})")

    def _hotkey_event(self, kind: str) -> None:
        # пока в настройках пишется комбинация, старые хоткеи не срабатывают
        if hotkey_capture.is_active():
            return
        self.events.put((kind, None))

    def on_hotkey(self) -> None:
        if self.config.get("hotkey_mode") == "hold":
            self.start_push_to_talk()
        else:
            self.toggle_hotkey()

    def toggle_hotkey(self) -> None:
        """Режим переключателя: нажал — пишу, нажал ещё раз — стоп."""
        if self.recording and self.stop_event is not None:
            trace("hotkey: повторное нажатие, стоп")
            self.stop_event.set()
            return
        self._start_recording(push_to_talk=False, source="хоткей")

    def start_push_to_talk(self) -> None:
        """Хоткей: пишем, пока клавиши зажаты; повторное нажатие — стоп."""
        if self.recording and self.stop_event is not None:
            # второе нажатие останавливает всегда: раньше после 15 с опрос сбрасывал
            # ptt_active, и запись нельзя было остановить с клавиатуры вообще
            trace("ptt: повторное нажатие — останавливаю")
            self.stop_event.set()
            return
        self._start_recording(push_to_talk=True, source="хоткей")
        # опрос всегда: хук не видит клавиши, если активное окно запущено от админа
        threading.Thread(target=self._watch_release, args=(self.stop_event,), daemon=True).start()

    def _start_recording(self, push_to_talk: bool, source: str) -> None:
        """Старт записи. Распознавание предыдущей фразы не мешает — оно в очереди."""
        if self.recording:
            return
        if not self._disk_ready():
            return
        self._remember_target()
        self.stop_event = threading.Event()
        self.recording = True
        self.ptt_active = push_to_talk
        self._set_record_state(True)
        self._overlay_recording()
        hint = ("Слушаю… отпустите клавиши или нажмите хоткей ещё раз" if push_to_talk
                else "Слушаю… нажмите хоткей ещё раз, чтобы остановить")
        self.status.set(hint + self._queue_suffix())
        path = history.take_path()
        self._last_take = path
        trace(f"запись ({source}): {path}")
        threading.Thread(target=self._capture, args=(path, push_to_talk), daemon=True).start()

    def _queue_suffix(self) -> str:
        depth = self.queue.depth
        return f" · в очереди {depth}" if depth else ""

    def _disk_ready(self) -> bool:
        """Очередь записей не должна забить диск: при нехватке места не начинаем."""
        try:
            free = shutil.disk_usage(str(DATA)).free
        except OSError:
            return True
        if free < 200 * 2 ** 20:
            self.status.set("Мало места на диске — освободите хотя бы 200 МБ")
            self._overlay_show("Мало места на диске", "#e05555", 2500)
            return False
        return True

    def _capture(self, path, push_to_talk: bool) -> None:
        """Пишет реплику в файл и ставит её в очередь распознавания."""
        try:
            self.recorder.record_to_file(
                path,
                silence_ms=int(self.config["silence_ms"])
                if self.config.get("auto_stop", True) else 0,
                on_level=self._level_cb,
                push_to_talk=push_to_talk,
                stop_event=self.stop_event,
            )
            lost = self.recorder.last_overflow_ms
            if lost >= 500:
                self.events.put(("status", f"Потеряно {lost} мс звука — проверьте текст"))
            self.events.put(("captured", path))
            self.queue.submit(path, self.paste_target)
            self.events.put(("queue_state", self.queue.depth))
        except RecorderError as exc:
            trace(f"запись не удалась: {exc}")
            self.events.put(("status", str(exc)))
            self.events.put(("overlay", (str(exc)[:60], "#e05555", 2500)))
        except Exception as exc:
            trace_exc("recording error")
            self.events.put(("status", f"Ошибка записи: {exc}"))
        finally:
            self.recording = False
            self.ptt_active = False
            self.events.put(("reset_button", None))

    def _transcribe_file(self, path, cancel) -> str:
        """Вызывается из рабочего потока очереди: без интерфейса и без Tk."""
        text = self.engine.transcribe_file(path,
                                          chunk_seconds=float(self.config["chunk_seconds"]),
                                          cancel=cancel)
        return tidy(apply_replacements(text, self.config.get("replacements") or {}))

    def _queue_text(self, text: str, task: dict) -> None:
        self.events.put(("text", (text, task.get("target"))))

    def _queue_error(self, exc: Exception, task: dict) -> None:
        trace_exc(f"transcribe error ({type(exc).__name__})")
        self.events.put(("take_error", (str(exc), task.get("path"))))

    def _queue_state(self, depth: int) -> None:
        self.events.put(("queue_state", depth))

    def retry_last(self) -> None:
        """Повторить распознавание последней записи — микрофон не нужен."""
        path = self._last_take
        if path is None or not path.exists():
            self.status.set("Нет записи для повтора")
            return
        self.queue.submit(path, self.paste_target)
        self.status.set("Повторное распознавание…")

    def cancel_recognition(self) -> None:
        """Снимает очередь и прерывает текущую фразу."""
        dropped = self.queue.cancel_all()
        self._overlay_stop()
        self._overlay_show("Распознавание отменено", "#e0a33a", 2000)
        self.status.set(f"Распознавание отменено (снято задач: {dropped})")

    def _watch_release(self, event) -> None:
        """Стоп по отпусканию клавиш фактической комбинации.

        Хук для этого не используется: он не получает нажатий, когда активное окно
        запущено от администратора. Раньше здесь были зашиты Ctrl+Alt+Space, из-за
        чего режим «удерживать» не работал ни с каким другим хоткеем.
        """
        try:
            keys = HOTKEYS.keys("dictate")
        except ValueError:
            keys = []
        if not keys:
            return
        started = time.monotonic()
        seen_held = False
        while not event.is_set():
            held = all(USER32.GetAsyncKeyState(vk) & 0x8000 for vk in keys)
            if held:
                seen_held = True
            elif seen_held or time.monotonic() - started > 0.3:
                break
            if time.monotonic() - started > 1900:      # страховка от забытой записи
                break
            time.sleep(0.025)
        self.ptt_active = False
        if not event.is_set():
            trace("ptt: клавиши отпущены, стоп")
            event.set()

    def _level_cb(self, db: float, voiced: bool) -> None:
        # -60..0 дБ -> 0..100
        self.events.put(("level", max(0.0, min(100.0, (db + 60) * 100 / 60))))

    # ---------- события из потоков ----------
    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.set(payload)
                    trace(f"status: {payload}")
                elif kind == "text":
                    text, target = payload if isinstance(payload, tuple) else (payload, None)
                    if text:
                        self._append(text, target)
                    else:
                        trace("text: пустая строка от модели")
                        self.status.set("Речь не распознана — тишина или шум")
                        self._overlay_show("Ничего не распознано", "#e0a33a", 2000)
                elif kind == "captured":
                    self._overlay_processing(self.queue.depth + 1)
                elif kind == "queue_state":
                    self._on_queue_state(payload)
                elif kind == "take_error":
                    self._on_take_error(*payload)
                elif kind == "level":
                    percent = max(0.0, min(100.0, payload))
                    self.level.set(percent / 100.0)
                    if self.recording:
                        self._overlay_level(percent)
                elif kind == "error":
                    messagebox.showerror("Голосовой ввод", payload)
                elif kind == "hotkey":
                    # окно не поднимаем: фокус должен остаться в том приложении, куда диктуем
                    self.on_hotkey()
                elif kind == "tray_ready":
                    self.tray_ready = True
                    if self.start_hidden:
                        self.hide_to_tray()
                        try:
                            self.icon.notify(
                                f"Работает в трее. Диктовка — {HOTKEYS.shown('dictate')}.",
                                "Голосовой ввод")
                        except Exception:
                            pass
                elif kind == "update_ready":
                    self._offer_update(payload)
                elif kind == "update_progress":
                    done, total = payload
                    if self._update_dialog is not None:
                        self._update_dialog.set_progress(done, total)
                elif kind == "update_downloaded":
                    self._close_update_dialog()
                    self._install_update(payload)
                elif kind == "update_cancelled":
                    self._close_update_dialog()
                    self._update_busy = False
                    self.status.set("Скачивание обновления отменено")
                elif kind == "update_failed":
                    self._close_update_dialog()
                    self._update_busy = False
                    self.status.set(f"Обновление не скачалось: {payload}")
                    messagebox.showerror("Обновление",
                                         f"Не удалось скачать обновление:\n{payload}",
                                         parent=self.root)
                elif kind == "overlay":
                    self._overlay_show(*payload)
                elif kind == "hotkey_changed":
                    self._on_hotkey_changed(*payload)
                elif kind == "toggle":
                    self.toggle_recording()
                elif kind == "show":
                    self._show_window()
                elif kind == "hide_tray":
                    self.root.withdraw()
                    trace("окно спрятано в трей")
                elif kind == "quit":
                    self._quit()
                elif kind == "reset_button":
                    self._set_record_state(False)
                    self.level.set(0)
                    if not self.recording:
                        self._overlay_level(0)
                elif kind == "idle":
                    self._apply_pending_model()
        except queue.Empty:
            pass
        except Exception:
            # иначе цепочка after умирает и интерфейс «замерзает» без сообщений
            trace("UI ERROR: " + traceback.format_exc())
            self.status.set("Ошибка интерфейса — подробности в .cache/app.log")
        finally:
            self.root.after(80, self._drain_events)

    def _on_queue_state(self, depth: int) -> None:
        """Очередь сообщает, сколько фраз ещё ждут распознавания."""
        if depth:
            if not self.recording:
                self._overlay_processing(depth)
            self.status.set(f"Распознаю… (в очереди {depth})")
            return
        if not self.recording:
            self._overlay_stop()
        self._apply_pending_model()

    def _on_take_error(self, message: str, path) -> None:
        """Ошибка распознавания не должна стоить пользователю сказанной фразы."""
        self.status.set(f"Ошибка распознавания: {message}")
        self._overlay_stop()
        retry = messagebox.askyesno(
            "Голосовой ввод",
            f"Не удалось распознать запись:\n{message}\n\n"
            f"Запись сохранена, её можно повторить.\n"
            f"Подробности — в журнале:\n{LOG}",
            parent=self.root)
        if retry and path:
            self.queue.submit(path, self.paste_target)
            self.status.set("Повторное распознавание…")

    def _append(self, text: str, target=None) -> None:
        prefix = "" if self.text.index("end-1c") == "1.0" else "\n"
        self.text.insert("end", prefix + text)
        self.text.see("end")
        history.append_text(text)
        self._deliver(text, target)

    def _deliver(self, text: str, target=None) -> None:
        """Кладём в буфер и, если включено, вставляем в окно, активное до записи."""
        if not set_clipboard_text(text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()

        if not self.autopaste.get():
            self.status.set("Текст в буфере обмена — вставьте Ctrl+V")
            self.events.put(("overlay", ("Скопировано в буфер", "#4caf50", 1500)))
            return
        target = target or self.paste_target
        current = USER32.GetForegroundWindow()
        # переключились, пока говорили — вставляем туда, где пользователь сейчас
        if current and USER32.IsWindow(current) and not self._is_own(current) and current != target:
            trace(f"paste: окно сменилось, беру текущее {current}")
            target = current
        if not target or self._is_own(target) or not USER32.IsWindow(target):
            # цель оказалась нашим окном (или её нет) — берём последнее чужое
            target = self.last_external
        if not target or self._is_own(target) or not USER32.IsWindow(target):
            self.status.set("Нет чужого окна для вставки — текст в буфере обмена")
            self.events.put(("overlay", ("Скопировано в буфер", "#4caf50", 1500)))
            return
        # SW_RESTORE разворачивает свёрнутое окно, но заодно снимает maximise —
        # поэтому трогаем окно только если оно действительно свёрнуто
        if USER32.IsIconic(target):
            USER32.ShowWindow(target, 9)
        if not self._focus_window(target):
            trace(f"paste: фокус не перешёл, foreground={USER32.GetForegroundWindow()}")
            self.status.set("Не удалось переключиться в окно — текст в буфере обмена")
            self.events.put(("overlay", ("Не удалось вставить — текст в буфере", "#e05555", 2500)))
            return
        time.sleep(0.05)
        foreground = USER32.GetForegroundWindow()
        trace(f"paste: foreground={foreground} target={target} "
              f"own={self._is_own(foreground)} '{self._window_title(target)}'")
        if foreground != target:
            self.status.set("Фокус ушёл из окна вставки — текст в буфере обмена")
            self.events.put(("overlay", ("Фокус ушёл — текст в буфере", "#e0a33a", 2000)))
            return
        injected = win32.send_ctrl_v(log=trace)
        trace(f"paste target={target} injected={injected}")
        if injected:
            title = win32.window_title(target).strip()
            short = (title[:28] + "…") if len(title) > 28 else title
            self.status.set(f"Вставлено в «{short}»" if short else "Вставлено в активное окно")
            self.events.put(("overlay", (f"Вставлено в {short}" if short else "Вставлено",
                                         "#4caf50", 1500)))
        else:
            self.status.set("Система не дала вставить — текст в буфере обмена (Ctrl+V)")
            self.events.put(("overlay", ("Вставка заблокирована — текст в буфере", "#e05555", 2500)))

    def _window_title(self, hwnd: int) -> str:
        return win32.window_title(hwnd)

    def _focus_window(self, target: int, attempts: int = 3) -> bool:
        """Переключаем фокус и проверяем, что он действительно у цели."""
        return win32.focus_window(target, attempts)

    def copy_text(self) -> None:
        content = self.text.get("1.0", "end-1c")
        if not set_clipboard_text(content):
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
        self.status.set("Текст скопирован в буфер обмена")

    def clear_text(self) -> None:
        self.text.delete("1.0", "end")
        self.status.set("Очищено")

    def log(self, message: str) -> None:
        trace(f"log: {message}")
        self.events.put(("status", message))

    def _model_progress(self, percent: int) -> None:
        step = getattr(self, "_last_percent", -10)
        if percent - step < 2 and percent < 100:
            return
        self._last_percent = percent
        text = f"Загрузка модели {percent}%"
        self.events.put(("status", text))
        self.events.put(("overlay", (text, "#4caf50", 30000)))

    def _on_close(self) -> None:
        # крестик прячет окно в трей, выход — через меню иконки
        if self.icon is not None and not self.quitting:
            self.hide_to_tray()
        else:
            self.root.destroy()


def main() -> None:
    try:
        _main()
    except Exception:
        try:
            crash = DATA / ".cache" / "crash.log"
            crash.parent.mkdir(parents=True, exist_ok=True)
            crash.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise


def _main() -> None:
    if "--uninstall" in sys.argv:
        from uninstall import run_uninstall
        sys.exit(run_uninstall())
    try:
        marker = DATA / ".cache" / "start.marker"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} argv={sys.argv}\n",
                          encoding="utf-8")
    except OSError:
        pass  # маркер нужен только для диагностики, падать из-за него нельзя
    root = ctk.CTk()
    if win32.already_running(os.environ.get("V2T_ALLOW_MULTI") == "1"):
        # второй запуск просто поднимает окно первого, без окон-тупиков
        if not win32.request_show():
            root.withdraw()
            messagebox.showinfo("Голосовой ввод",
                                "Приложение уже запущено — ищите иконку в трее.")
        root.destroy()
        return
    config = Config.load(on_error=trace)
    app = App(root, config)
    win32.start_show_listener(lambda: app.events.put(("show", None)))
    # окно сворачивается только когда иконка трея реально создалась (см. tray_ready)
    app.start_hidden = bool(config["start_minimized"]) and "--show" not in sys.argv
    root.mainloop()


if __name__ == "__main__":
    main()
