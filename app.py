"""Голосовой ввод: микрофон -> GigaAM -> текст (Tkinter, офлайн)."""

from __future__ import annotations

import ctypes
import datetime as dt
import os
import queue
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
from config import Config
import hotkey_capture
from hotkeys import MOD_NOREPEAT, parse_hotkey
from postprocess import apply_replacements, tidy
from recorder import MicRecorder, RecorderError
import update
from version import VERSION

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

MODELS = ["v3_e2e_rnnt", "v3_e2e_ctc", "v3_rnnt", "v3_ctc"]


def _input_devices() -> list[str]:
    try:
        from recorder import list_input_devices
        return ["По умолчанию"] + list_input_devices()
    except Exception:
        return ["По умолчанию"]


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


USER32 = ctypes.windll.user32

# 64-битные дескрипторы окон: без argtypes ctypes обрезает их до 32 бит и вызовы
# молча ничего не делают (окно «показывается», но остаётся на месте)
USER32.GetForegroundWindow.restype = ctypes.c_void_p
USER32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
USER32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
USER32.GetAncestor.restype = ctypes.c_void_p
USER32.IsWindow.argtypes = [ctypes.c_void_p]
USER32.IsIconic.argtypes = [ctypes.c_void_p]
USER32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
USER32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
USER32.GetWindowThreadProcessId.restype = ctypes.c_ulong
USER32.BringWindowToTop.argtypes = [ctypes.c_void_p]
USER32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
USER32.GetWindowLongW.restype = ctypes.c_long
USER32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
USER32.SetWindowLongW.restype = ctypes.c_long
USER32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
USER32.SetWindowPos.restype = ctypes.c_bool
USER32.IsWindowVisible.argtypes = [ctypes.c_void_p]
USER32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
USER32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
USER32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]

VK_CONTROL, VK_V = 0x11, 0x56
VK_MENU, VK_SPACE = 0x12, 0x20
KEYEVENTF_KEYUP = 0x0002


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", ctypes.c_ulong),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_ushort), ("wParamH", ctypes.c_ushort)]


class _InputUnion(ctypes.Union):
    # размер юниона должен быть как у MOUSEINPUT (32 байта), иначе SendInput
    # отвергает весь массив с ERROR_INVALID_PARAMETER и ничего не вставляет
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _InputUnion)]


USER32.SendInput.restype = ctypes.c_uint
USER32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
VK_SPACE = 0x20
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_ulong), ("pt", POINT)]


HOTKEY_TID = {"tid": 0}
WM_RELOAD_HOTKEYS = 0x8000 + 1

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_KEYUP, WM_SYSKEYUP = 0x0101, 0x0105
VK_LWIN, VK_RWIN = 0x5B, 0x5C
HOOK_EVENT = {"event": None}
HOOK_OK = {"ok": False}


USER32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
USER32.SetWindowsHookExW.restype = ctypes.c_void_p
USER32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
USER32.CallNextHookEx.restype = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_ulong), ("scanCode", ctypes.c_ulong),
                ("flags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)


def _hook_proc(n_code, w_param, l_param):
    """Запись комбинации и отпускание клавиш для режима рации."""
    if n_code == 0:
        vk = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
        if hotkey_capture.is_active():
            hotkey_capture.feed_key(vk, w_param)
            return 1  # пока пишем комбинацию, клавиши не уходят в окна
        if w_param in (WM_KEYUP, WM_SYSKEYUP):
            if vk in (VK_CONTROL, VK_MENU, VK_SPACE):
                event = HOOK_EVENT["event"]
                if event is not None and not event.is_set():
                    event.set()
    return USER32.CallNextHookEx(None, n_code, w_param, l_param)


def start_keyboard_hook() -> None:
    def run() -> None:
        global HOOK_PROC
        HOOK_PROC = HOOKPROC(_hook_proc)
        handle = USER32.SetWindowsHookExW(WH_KEYBOARD_LL, HOOK_PROC, None, 0)
        if not handle:
            trace(f"hook: не установлен, ошибка {ctypes.windll.kernel32.GetLastError()}")
            return
        HOOK_OK["ok"] = True
        hotkey_capture.HOOK_READY["ok"] = True
        trace("hook: клавиатурный хук установлен")
        msg = MSG()
        while USER32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    threading.Thread(target=run, daemon=True).start()

# строки комбинаций и их коды: меняются из настроек, читаются потоком хоткеев
HOTKEY_COMBOS = {"dictate": "ctrl+shift+space", "show": "ctrl+shift+w"}
HOTKEY_SPECS = {name: parse_hotkey(combo) for name, combo in HOTKEY_COMBOS.items()}
HOTKEY_ACTUAL = dict(HOTKEY_COMBOS)
HOTKEY_FALLBACKS = {
    "dictate": ["ctrl+shift+space", "ctrl+shift+f9", "ctrl+alt+d"],
    "show": ["ctrl+shift+w", "ctrl+shift+f10", "ctrl+alt+o"],
}


def apply_hotkey_specs(dictate: str, show: str) -> None:
    """Разбирает комбинации в коды WinAPI (ошибка разбора — умолчание)."""
    for name, combo in (("dictate", dictate), ("show", show)):
        try:
            mods, vk = parse_hotkey(combo)
        except ValueError:
            combo = HOTKEY_FALLBACKS[name][0]
            mods, vk = parse_hotkey(combo)
        HOTKEY_COMBOS[name] = combo
        HOTKEY_SPECS[name] = (mods, vk)


def set_hotkeys(dictate: str, show: str) -> None:
    """Обновляет комбинации и просит поток слушателя перерегистрировать их."""
    apply_hotkey_specs(dictate, show)
    if HOTKEY_TID["tid"]:
        USER32.PostThreadMessageW(HOTKEY_TID["tid"], WM_RELOAD_HOTKEYS, 0, 0)


def start_hotkey_listener(on_dictate, on_show, on_status=None) -> threading.Thread:
    """Глобальные горячие клавиши: диктовка и показ окна."""

    def register() -> None:
        for hotkey_id, name in ((1, "dictate"), (2, "show")):
            candidates = [HOTKEY_COMBOS[name]] + [c for c in HOTKEY_FALLBACKS[name]
                                                  if c != HOTKEY_COMBOS[name]]
            registered = None
            for combo in candidates:
                mods, vk = parse_hotkey(combo)
                if USER32.RegisterHotKey(None, hotkey_id, mods, vk):
                    registered = combo
                    break
                trace(f"hotkey: {combo} занят, ошибка {ctypes.windll.kernel32.GetLastError()}")
            if registered:
                HOTKEY_ACTUAL[name] = registered
                trace(f"hotkey: {name} = {registered}")
            else:
                trace(f"hotkey: {name} не удалось назначить ни одну комбинацию")
                if on_status:
                    on_status(name, None)
                continue
            if registered != HOTKEY_COMBOS[name] and on_status:
                on_status(name, registered)

    def run() -> None:
        HOTKEY_TID["tid"] = ctypes.windll.kernel32.GetCurrentThreadId()
        register()
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
        USER32.UnregisterHotKey(None, 1)
        USER32.UnregisterHotKey(None, 2)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def force_foreground(hwnd: int) -> bool:
    USER32.BringWindowToTop(hwnd)
    if USER32.SetForegroundWindow(hwnd):
        return True
    # обход блокировки смены фокуса: цепляемся к потоку текущего foreground-окна
    foreground = USER32.GetForegroundWindow()
    if not foreground:
        return False
    target_thread = USER32.GetWindowThreadProcessId(foreground, None)
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    USER32.AttachThreadInput(current_thread, target_thread, True)
    try:
        return bool(USER32.SetForegroundWindow(hwnd))
    finally:
        USER32.AttachThreadInput(current_thread, target_thread, False)


def send_ctrl_v() -> bool:
    """Отправляет Ctrl+V в активное окно. False — если система заблокировала ввод."""
    events = [INPUT(type=1, u=_InputUnion(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))
              for vk, flags in ((VK_CONTROL, 0), (VK_V, 0), (VK_V, KEYEVENTF_KEYUP), (VK_CONTROL, KEYEVENTF_KEYUP))]
    array = (INPUT * len(events))(*events)
    kernel32 = ctypes.windll.kernel32
    kernel32.SetLastError(0)
    sent = USER32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent == len(events):
        return True

    error = kernel32.GetLastError()
    trace(f"SendInput: {sent}/{len(events)}, ошибка {error}, sizeof(INPUT)={ctypes.sizeof(INPUT)}")
    # 5 — UIPI (окно с большими правами), 87 — неверная структура, 0 — ввод перехвачен
    USER32.keybd_event(VK_CONTROL, 0, 0, 0)
    USER32.keybd_event(VK_V, 0, 0, 0)
    USER32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    USER32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    return False


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
        self._capture = None          # активная запись комбинации в панели
        self._apply_after = None      # отложенное применение настроек
        self._applying = False
        self._update_busy = False     # проверка/скачивание уже идёт
        self._update_cancel = None
        self._update_info = None
        self._update_dialog = None
        LOG.parent.mkdir(exist_ok=True)
        LOG.write_text(f"=== старт {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===\n", encoding="utf-8")
        self._log_environment()
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
        if self.config.get("check_updates", True):
            self.root.after(15000, self._auto_update_check)

    # ---------- обновления ----------
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
                                      expected_size=info.get("size") or 0)
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
            helper = update.apply(archive, update.current_target(), pid=os.getpid())
        except Exception as exc:
            trace_exc("установка обновления")
            self._update_busy = False
            self._close_update_dialog()
            self.status.set(f"Обновление не установлено: {exc}")
            messagebox.showerror("Обновление",
                                 f"Не удалось запустить установку:\n{exc}", parent=self.root)
            return
        trace(f"обновление запущено через {helper}, закрываю приложение")
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
        self.var_model = tk.StringVar(value=self.config["model"])
        model_box = ctk.CTkComboBox(panel, values=MODELS, variable=self.var_model,
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

        self.var_hotkey = tk.StringVar(value=self.config["hotkey"])
        self._hotkey_row("Хоткей диктовки", self.var_hotkey)
        self.var_show = tk.StringVar(value=self.config["show_hotkey"])
        self._hotkey_row("Хоткей окна", self.var_show)
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

        self._panel_label("Максимум записи, с", top=6)
        self.var_max = tk.IntVar(value=int(self.config["max_seconds"]))
        entry_max = ctk.CTkEntry(panel, textvariable=self.var_max, width=90, corner_radius=10,
                                 fg_color=BG, border_color=BORDER)
        entry_max.pack(anchor="w", padx=16, pady=(0, 4))
        self._bind_apply_on_leave(entry_max)
        self._panel_hint("Без автостопа пишем до «Стоп» или этого лимита.")

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
        devices = _input_devices()
        if self.config.get("device") is None:
            return "По умолчанию"
        for item in devices:
            if item.startswith(f"{self.config['device']}:"):
                return item
        return "По умолчанию"

    def _on_auto_stop_toggle(self) -> None:
        self.entry_silence.configure(state="normal" if self.var_auto_stop.get() else "disabled")
        self._schedule_apply()

    def _hotkey_row(self, title: str, var: tk.StringVar) -> None:
        self._panel_label(title)
        holder = ctk.CTkFrame(self.settings_panel, fg_color="transparent")
        holder.pack(fill="x", padx=16, pady=(0, 4))
        entry = ctk.CTkEntry(holder, textvariable=var, corner_radius=10, fg_color=BG,
                             border_color=BORDER, font=ctk.CTkFont("Segoe UI", 12))
        entry.pack(side="left", fill="x", expand=True)
        self._bind_apply_on_leave(entry)
        mark = ctk.CTkLabel(holder, text="", width=16, font=ctk.CTkFont("Segoe UI", 12))
        mark.pack(side="left", padx=(6, 0))
        ctk.CTkButton(holder, text="Записать", width=88, height=28, corner_radius=9,
                      fg_color=BG, hover_color=CARD_HOVER, border_width=1, border_color=BORDER,
                      text_color=TEXT, font=ctk.CTkFont("Segoe UI", 11),
                      command=lambda: self._start_capture(var, mark)).pack(side="left", padx=(6, 0))

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
            limit = max(10, int(self.var_max.get()))
            device_text = self.var_device.get()
            device = None if device_text == "По умолчанию" else int(device_text.split(":", 1)[0])
        except (ValueError, tk.TclError):
            self.status.set("Проверьте числа: пауза и максимум записи")
            return
        if self.var_auto_stop.get() and silence >= limit * 1000:
            self.status.set("Пауза больше максимальной длительности")
            return

        values = {
            "model": self.var_model.get(),
            "device": device,
            "hotkey": dict_combo,
            "show_hotkey": show_combo,
            "hotkey_mode": "hold" if self.var_mode.get() == "удерживать" else "toggle",
            "silence_ms": silence,
            "auto_stop": bool(self.var_auto_stop.get()),
            "max_seconds": limit,
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
                self.engine = GigaAMEngine(model_name=self.config["model"], log=self.log,
                                          on_progress=self._model_progress)
                threading.Thread(target=self._warm_up, daemon=True).start()
        finally:
            self._applying = False
        self.status.set("Настройки сохранены")

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
                                 f"Голосовой ввод — {HOTKEY_ACTUAL['dictate']}", menu)
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
        self.overlay_dot.pack(side="left", padx=(16, 10), pady=10)
        self.overlay_text = tk.Label(frame, text="", font=("Segoe UI", 12), bg=CARD, fg=TEXT)
        self.overlay_text.pack(side="left", padx=(0, 18), pady=10)

        self.overlay_after = None
        for widget in (self.overlay, frame, self.overlay_text, self.overlay_dot):
            widget.bind("<Button-1>", lambda _e: self._show_window())

        self.overlay.update_idletasks()
        hwnd = USER32.GetAncestor(self.overlay.winfo_id(), 2) or self.overlay.winfo_id()
        style = USER32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        USER32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.overlay_hwnd = hwnd

    def _overlay_show(self, text: str, color: str = "#e05555", ttl_ms: int = 1500) -> None:
        if self.overlay is None:
            return
        self.overlay_text.configure(text=text)
        self.overlay_dot.configure(fg=color)
        self.overlay.update_idletasks()
        width = max(self.overlay.winfo_reqwidth(), 220)
        height = self.overlay.winfo_reqheight()
        x = (self.overlay.winfo_screenwidth() - width) // 2
        y = self.overlay.winfo_screenheight() - height - 96
        # geometry у overrideredirect-окна применяется ненадёжно — ставим через WinAPI
        USER32.SetWindowPos(self.overlay_hwnd, -1, x, y, width, height,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)
        if self.overlay_after is not None:
            self.root.after_cancel(self.overlay_after)
        self.overlay_after = self.root.after(ttl_ms, self._overlay_hide)

    def _overlay_hide(self) -> None:
        self.overlay_after = None
        if self.overlay is not None:
            self.overlay.withdraw()

    def hide_to_tray(self) -> None:
        # сворачиваем на панель задач, а не прячем полностью: иконка в трее
        # у Windows 11 часто уезжает в переполнение «^», и окно было бы не вернуть
        self.root.iconify()
        trace("окно свёрнуто на панель задач")
        self.events.put(("overlay", (f"Свёрнуто · {HOTKEY_ACTUAL['show']} — открыть",
                                     "#4caf50", 4000)))

    def _show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit(self) -> None:
        self.quitting = True
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
        file_menu.add_command(label="Свернуть", command=self.hide_to_tray)
        file_menu.add_command(label="Выход", command=self._quit)
        menubar.add_cascade(label="Файл", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Проверить обновления…", command=self.check_updates)
        help_menu.add_separator()
        help_menu.add_command(label="Диагностика", command=self.run_diagnostics)
        help_menu.add_command(label="Открыть журнал", command=self.open_log)
        help_menu.add_command(label="Открыть папку данных", command=self.open_data_folder)
        help_menu.add_command(label="О программе", command=self.show_about)
        menubar.add_cascade(label="Справка", menu=help_menu)
        self.root.config(menu=menubar)

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
            f"Диктовка: {HOTKEY_ACTUAL['dictate']}\n"
            f"Показать окно: {HOTKEY_ACTUAL['show']}",
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
        return f"● Записать ({HOTKEY_ACTUAL['dictate']})"

    def _stop_button_text(self) -> str:
        return f"■ Стоп ({HOTKEY_ACTUAL['dictate']})"

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
        if self.ptt_active and self.stop_event is not None:
            self.stop_event.set()
            return
        if self.busy:
            return
        if not self.recording:
            self.stop_event = threading.Event()   # без него кнопка «Стоп» не останавливает
            self._remember_target()
            self.recording = True
            self.busy = True
            self._set_record_state(True)
            self.status.set("Слушаю…")
            self.events.put(("overlay", ("Запись…", "#e05555", 15000)))
            threading.Thread(target=self._record_and_transcribe, daemon=True).start()

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
        if self.busy:
            return
        self._remember_target()
        self.stop_event = threading.Event()
        self.recording = True
        self.busy = True
        self._set_record_state(True)
        self.status.set("Слушаю… нажмите хоткей ещё раз, чтобы остановить")
        self.events.put(("overlay", ("Запись… ещё раз — стоп", "#e05555", 25000)))
        threading.Thread(target=self._record_and_transcribe, args=(False,), daemon=True).start()

    def start_push_to_talk(self) -> None:
        """Хоткей: пишем, пока клавиши зажаты; повторное нажатие — стоп."""
        if self.ptt_active and self.stop_event is not None:
            # аварийная остановка: если отпускание не поймали (чужое окно с правами
            # администратора), хоткей работает как «стоп»
            trace("ptt: повторное нажатие — останавливаю")
            self.stop_event.set()
            return
        if self.busy:
            trace("ptt: занято, пропускаю")
            return
        self.ptt_active = True
        self._remember_target()
        self.stop_event = threading.Event()
        HOOK_EVENT["event"] = self.stop_event
        self.recording = True
        self.busy = True
        self._set_record_state(True)
        self.status.set("Слушаю… отпустите клавиши или нажмите хоткей ещё раз")
        self.events.put(("overlay", ("Запись… отпустите клавиши или нажмите хоткей ещё раз",
                                     "#e05555", 25000)))
        threading.Thread(target=self._record_and_transcribe, args=(True,), daemon=True).start()
        # опрос всегда: хук не видит клавиши, если активное окно запущено от админа
        threading.Thread(target=self._watch_release, args=(self.stop_event,), daemon=True).start()

    def _watch_release(self, event: threading.Event) -> None:
        """Стоп по отпусканию, но только если мы реально видели клавиши зажатыми."""
        time.sleep(0.05)
        seen_held = False
        deadline = time.monotonic() + 15.0
        while not event.is_set() and time.monotonic() < deadline:
            held = all(USER32.GetAsyncKeyState(vk) & 0x8000
                       for vk in (VK_CONTROL, VK_MENU, VK_SPACE))
            if held:
                seen_held = True
            elif seen_held:
                self.ptt_active = False
                event.set()
                return
            time.sleep(0.03)
        self.ptt_active = False

    def _record_and_transcribe(self, push_to_talk: bool = False) -> None:
        try:
            audio = self.recorder.record(
                max_seconds=float(self.config["max_seconds"]),
                silence_ms=int(self.config["silence_ms"])
                if self.config.get("auto_stop", True) else 0,
                on_level=self._level_cb,
                push_to_talk=push_to_talk,
                stop_event=self.stop_event,
            )
            trace(f"record done: {len(audio)} сэмплов, dtype={audio.dtype}")
            self.events.put(("status", "Распознаю…"))
            self.events.put(("overlay", ("Распознаю…", "#e0a33a", 15000)))
            text = self.engine.transcribe(audio, chunk_seconds=float(self.config["chunk_seconds"]))
            text = tidy(apply_replacements(text, self.config.get("replacements") or {}))
            trace(f"transcribe done: {len(text)} символов")
            self.events.put(("text", text))
            if not text:
                self.events.put(("status", "Речь не распознана — тишина или шум"))
                self.events.put(("overlay", ("Ничего не распознано", "#e0a33a", 2000)))
        except RecorderError as exc:
            trace_exc("recorder error")
            self.events.put(("status", str(exc)))
            self.events.put(("overlay", (str(exc)[:60], "#e05555", 2500)))
        except Exception as exc:
            # трассировка обязательна: по одному сообщению WinError причина не видна
            trace_exc(f"transcribe error ({type(exc).__name__})")
            self.events.put(("status", f"Ошибка распознавания: {exc}"))
            self.events.put(("error", f"Не удалось распознать:\n{exc}\n\nПодробности — в журнале:\n{LOG}"))
        finally:
            HOOK_EVENT["event"] = None
            self.recording = False
            self.busy = False
            self.ptt_active = False
            self.events.put(("reset_button", None))

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
                    if payload:
                        self._append(payload)
                    else:
                        trace("text: пустая строка от модели")
                elif kind == "level":
                    self.level.set(max(0.0, min(1.0, payload / 100.0)))
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
                                f"Работает в трее. Диктовка — {HOTKEY_ACTUAL['dictate']}.",
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
                    name, combo = payload
                    if combo:
                        self.status.set(f"Комбинация занята, {name} теперь {combo}")
                        self.events.put(("overlay", (f"Хоткей заменён на {combo}", "#e0a33a", 3000)))
                    else:
                        self.status.set(f"Не удалось назначить хоткей {name}")
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
        except queue.Empty:
            pass
        except Exception:
            # иначе цепочка after умирает и интерфейс «замерзает» без сообщений
            trace("UI ERROR: " + traceback.format_exc())
            self.status.set("Ошибка интерфейса — подробности в .cache/app.log")
        finally:
            self.root.after(80, self._drain_events)

    def _append(self, text: str) -> None:
        prefix = "" if self.text.index("end-1c") == "1.0" else "\n"
        self.text.insert("end", prefix + text)
        self.text.see("end")
        self._deliver(text)

    def _deliver(self, text: str) -> None:
        """Кладём в буфер и, если включено, вставляем в окно, активное до записи."""
        if not set_clipboard_text(text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()

        if not self.autopaste.get():
            self.status.set("Текст в буфере обмена — вставьте Ctrl+V")
            self.events.put(("overlay", ("Скопировано в буфер", "#4caf50", 1500)))
            return
        target = self.paste_target
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
        injected = send_ctrl_v()
        trace(f"paste target={target} injected={injected}")
        if injected:
            self.status.set("Вставлено в активное окно")
            self.events.put(("overlay", ("Вставлено", "#4caf50", 1200)))
        else:
            self.status.set("Система не дала вставить — текст в буфере обмена (Ctrl+V)")
            self.events.put(("overlay", ("Вставка заблокирована — текст в буфере", "#e05555", 2500)))

    def _window_title(self, hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, 256)
        return buffer.value

    def _focus_window(self, target: int, attempts: int = 3) -> bool:
        """Переключаем фокус и проверяем, что он действительно у цели."""
        for _ in range(attempts):
            if USER32.GetForegroundWindow() == target:
                return True
            force_foreground(target)
            time.sleep(0.12)
        return USER32.GetForegroundWindow() == target

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


SINGLE_INSTANCE_HANDLE = None


def already_running() -> bool:
    """Один экземпляр: иначе два окна делят хоткей, лог и файлы модели."""
    if os.environ.get("V2T_ALLOW_MULTI") == "1":
        return False
    global SINGLE_INSTANCE_HANDLE
    kernel32 = ctypes.windll.kernel32
    SINGLE_INSTANCE_HANDLE = kernel32.CreateMutexW(None, False, "Local\\voice2text-single")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


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
    try:
        marker = DATA / ".cache" / "start.marker"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} argv={sys.argv}\n",
                          encoding="utf-8")
    except OSError:
        pass  # маркер нужен только для диагностики, падать из-за него нельзя
    root = ctk.CTk()
    if already_running():
        root.withdraw()
        messagebox.showinfo("Голосовой ввод",
                            "Приложение уже запущено — ищите иконку в трее.")
        root.destroy()
        return
    config = Config.load()
    app = App(root, config)
    # окно сворачивается только когда иконка трея реально создалась (см. tray_ready)
    app.start_hidden = bool(config["start_minimized"]) and "--show" not in sys.argv
    root.mainloop()


if __name__ == "__main__":
    main()
