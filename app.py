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

from asr_engine import GigaAMEngine
from clipboard import set_text as set_clipboard_text
from config import Config
import hotkey_capture
from hotkeys import (MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN,
                     parse_hotkey)
from postprocess import apply_replacements, tidy
from recorder import MicRecorder, RecorderError
from settings_ui import open_settings
from version import VERSION

from paths import DATA, ROOT


def _ensure_streams() -> None:
    """В windowed-сборке sys.stdout/stderr = None, и gigaam/tqdm падают на записи."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


_ensure_streams()

LOG = DATA / ".cache" / "app.log"


def trace(message: str) -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


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

# комбинации только из модификаторов (alt+win, ctrl+alt…): RegisterHotKey их не умеет,
# поэтому ловим хуком. mask=0 — действие не на хуке.
HOOK_MASKS = {"dictate": 0, "show": 0}
HOOK_FIRED = {"dictate": False, "show": False}
HOOK_CALLBACKS: dict[str, object] = {}
MOD_VKS = {0x11, 0xA2, 0xA3, 0x12, 0xA4, 0xA5, 0x10, 0xA0, 0xA1,
           VK_LWIN, VK_RWIN}


USER32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
USER32.SetWindowsHookExW.restype = ctypes.c_void_p
USER32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
USER32.CallNextHookEx.restype = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_ulong), ("scanCode", ctypes.c_ulong),
                ("flags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)


def _pressed_mods() -> int:
    """Какие модификаторы зажаты прямо сейчас (по состоянию клавиатуры)."""
    mask = 0
    for vk, bit in ((0x11, MOD_CONTROL), (0x12, MOD_ALT), (0x10, MOD_SHIFT),
                    (VK_LWIN, MOD_WIN), (VK_RWIN, MOD_WIN)):
        if USER32.GetAsyncKeyState(vk) & 0x8000:
            mask |= bit
    return mask


def _hook_modifier(vk: int, w_param: int) -> bool:
    """Комбинация из одних модификаторов. True — событие не пускать дальше."""
    down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
    mask = _pressed_mods()
    fired = False
    for name, want in HOOK_MASKS.items():
        if not want:
            continue
        if down and mask == want and not HOOK_FIRED[name]:
            HOOK_FIRED[name] = True
            callback = HOOK_CALLBACKS.get(name)
            if callback:
                callback()
            fired = fired or name == "dictate"
        elif not down and mask != want:
            HOOK_FIRED[name] = False
    # меню Пуск не открываем, когда Win завершил комбинацию
    return fired and vk in (VK_LWIN, VK_RWIN)


def _hook_proc(n_code, w_param, l_param):
    """Отпускание клавиш для рации, комбинации из модификаторов, запись комбинации."""
    if n_code == 0:
        vk = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
        if hotkey_capture.is_active():
            hotkey_capture.feed_key(vk, w_param)
            return 1  # пока пишем комбинацию, клавиши не уходят в окна
        if vk in MOD_VKS and any(HOOK_MASKS.values()):
            if _hook_modifier(vk, w_param):
                return 1
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
    """Разбирает комбинации; чисто модификаторные уходят на перехват хуком."""
    for name, combo in (("dictate", dictate), ("show", show)):
        try:
            mods, vk = parse_hotkey(combo)
        except ValueError:
            mods, vk = parse_hotkey(HOTKEY_FALLBACKS[name][0])
        HOTKEY_COMBOS[name] = combo
        HOTKEY_SPECS[name] = (mods, vk)
        HOOK_MASKS[name] = (mods & ~MOD_NOREPEAT) if vk == 0 else 0
        HOOK_FIRED[name] = False


def set_hotkeys(dictate: str, show: str) -> None:
    """Обновляет комбинации и просит поток слушателя перерегистрировать их."""
    apply_hotkey_specs(dictate, show)
    if HOTKEY_TID["tid"]:
        USER32.PostThreadMessageW(HOTKEY_TID["tid"], WM_RELOAD_HOTKEYS, 0, 0)


def start_hotkey_listener(on_dictate, on_show, on_status=None) -> threading.Thread:
    """Глобальные горячие клавиши: диктовка и показ окна."""
    HOOK_CALLBACKS.update(dictate=on_dictate, show=on_show)

    def register() -> None:
        for hotkey_id, name in ((1, "dictate"), (2, "show")):
            if HOTKEY_SPECS[name][1] == 0:      # только модификаторы — умеет хук
                HOTKEY_ACTUAL[name] = HOTKEY_COMBOS[name]
                trace(f"hotkey: {name} = {HOTKEY_COMBOS[name]} (перехват хуком)")
                continue
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
        LOG.parent.mkdir(exist_ok=True)
        LOG.write_text(f"=== старт {dt.datetime.now():%Y-%m-%d %H:%M:%S} ===\n", encoding="utf-8")
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

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.root.title(f"Голосовой ввод — GigaAM {VERSION}")
        self.root.geometry("820x560")
        self.root.minsize(560, 420)
        self._build_menu()

        top = ttk.Frame(self.root, padding=(12, 12, 12, 6))
        top.pack(fill="x")

        self.btn_record = ttk.Button(top, text="● Записать (Пробел / Ctrl+Alt+Space)",
                                     command=self.toggle_recording, width=42)
        self.btn_record.pack(side="left")

        self.level = ttk.Progressbar(top, orient="horizontal", mode="determinate", maximum=100, length=220)
        self.level.pack(side="left", padx=12)

        self.btn_copy = ttk.Button(top, text="Копировать", command=self.copy_text)
        self.btn_copy.pack(side="right")
        self.btn_clear = ttk.Button(top, text="Очистить", command=self.clear_text)
        self.btn_clear.pack(side="right", padx=6)

        row2 = ttk.Frame(self.root, padding=(12, 0, 12, 4))
        row2.pack(fill="x")
        self.autopaste = tk.BooleanVar(value=bool(self.config["autopaste"]))
        ttk.Checkbutton(row2, text="Вставлять в активное окно (Ctrl+V)",
                        variable=self.autopaste).pack(side="left")
        ttk.Label(row2, text="текст всегда копируется в буфер и остаётся здесь",
                  foreground="#666").pack(side="left", padx=10)

        self.status = tk.StringVar(value="Загрузка модели…")
        ttk.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 6)).pack(fill="x")

        body = ttk.Frame(self.root, padding=(12, 0, 12, 12))
        body.pack(fill="both", expand=True)

        self.text = tk.Text(body, wrap="word", font=("Segoe UI", 12), undo=True)
        self.text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        scroll.pack(side="right", fill="y")
        self.text.configure(yscrollcommand=scroll.set)

        self.root.bind("<space>", self._on_space)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(300, self._remember_own_hwnd)

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
        draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(198, 40, 40, 255))
        draw.rounded_rectangle((26, 12, 38, 38), radius=6, fill=(255, 255, 255, 255))
        draw.rectangle((30, 38, 34, 46), fill=(255, 255, 255, 255))
        draw.rectangle((21, 46, 43, 51), fill=(255, 255, 255, 255))

        menu = pystray.Menu(
            pystray.MenuItem("Показать окно", lambda: self.events.put(("show", None)), default=True),
            pystray.MenuItem("Записать / Стоп", lambda: self.events.put(("toggle", None))),
            pystray.MenuItem("Спрятать в трей", lambda: self.events.put(("hide_tray", None))),
            pystray.MenuItem("Выход", lambda: self.events.put(("quit", None))),
        )
        self.icon = pystray.Icon("voice2text", image, "Голосовой ввод — Ctrl+Alt+Space", menu)
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
        self.overlay.configure(bg="#202124")

        frame = tk.Frame(self.overlay, bg="#202124")
        frame.pack(fill="both", expand=True)
        self.overlay_dot = tk.Label(frame, text="●", font=("Segoe UI", 18), bg="#202124", fg="#e05555")
        self.overlay_dot.pack(side="left", padx=(14, 8), pady=8)
        self.overlay_text = tk.Label(frame, text="", font=("Segoe UI", 12), bg="#202124", fg="#f1f1f1")
        self.overlay_text.pack(side="left", padx=(0, 16), pady=8)

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
        file_menu.add_command(label="Настройки…", command=self.open_settings_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Свернуть", command=self.hide_to_tray)
        file_menu.add_command(label="Выход", command=self._quit)
        menubar.add_cascade(label="Файл", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Диагностика", command=self.run_diagnostics)
        help_menu.add_command(label="О программе", command=self.show_about)
        menubar.add_cascade(label="Справка", menu=help_menu)
        self.root.config(menu=menubar)

    def open_settings_dialog(self) -> None:
        values = open_settings(self.root, self.config)
        if not values:
            return
        old_model = self.config["model"]
        self.config.update(values)
        self.config.save()

        self.autopaste.set(bool(self.config["autopaste"]))
        self.recorder.device = self.config.get("device")
        try:
            set_hotkeys(self.config["hotkey"], self.config["show_hotkey"])
        except ValueError as exc:
            messagebox.showerror("Настройки", f"Хоткей: {exc}", parent=self.root)
        self._apply_hook_combo()

        if self.config["model"] != old_model:
            self.engine = GigaAMEngine(model_name=self.config["model"], log=self.log,
                                      on_progress=self._model_progress)
            threading.Thread(target=self._warm_up, daemon=True).start()
        self.status.set("Настройки сохранены")

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

    def _track_foreground(self) -> None:
        """Запоминаем последнее чужое окно — туда и будем вставлять текст."""
        hwnd = USER32.GetForegroundWindow()
        if hwnd and hwnd != self.hwnd:
            self.last_external = hwnd
        self.root.after(400, self._track_foreground)

    def _on_space(self, event):
        if isinstance(event.widget, (tk.Text, ttk.Entry)):
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

    def toggle_recording(self) -> None:
        if self.ptt_active and self.stop_event is not None:
            self.stop_event.set()  # кнопка «Стоп» и меню трея останавливают рацию
            return
        if self.busy:
            return
        if not self.recording:
            self._remember_target()
            self.recording = True
            self.busy = True
            self.btn_record.configure(text="■ Стоп (Пробел)")
            self.status.set("Слушаю…")
            self.events.put(("overlay", ("Запись…", "#e05555", 15000)))
            threading.Thread(target=self._record_and_transcribe, daemon=True).start()

    def _remember_target(self) -> None:
        # цель вставки — окно, активное в момент старта записи
        foreground = USER32.GetForegroundWindow()
        self.paste_target = foreground if foreground and foreground != self.hwnd else self.last_external
        trace(f"paste target set: {self.paste_target}")

    def _hotkey_event(self, kind: str) -> None:
        # пока в настройках пишется комбинация, старые хоткеи не срабатывают
        if hotkey_capture.is_active():
            return
        self.events.put((kind, None))

    def _apply_hook_combo(self) -> None:
        # маски модификаторных комбинаций считает apply_hotkey_specs
        apply_hotkey_specs(self.config.get("hotkey") or "",
                           self.config.get("show_hotkey") or "")

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
        self.btn_record.configure(text="● Запись…")
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
        self.btn_record.configure(text="● Запись…")
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
                silence_ms=int(self.config["silence_ms"]),
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
            trace(f"recorder error: {exc}")
            self.events.put(("status", str(exc)))
            self.events.put(("overlay", (str(exc)[:60], "#e05555", 2500)))
        except Exception as exc:
            trace(f"transcribe error: {exc!r}")
            self.events.put(("status", f"Ошибка распознавания: {exc}"))
            self.events.put(("error", f"Не удалось распознать:\n{exc}"))
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
                    self.level["value"] = payload
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
                            self.icon.notify("Работает в трее. Диктовка — Ctrl+Alt+Space.", "Голосовой ввод")
                        except Exception:
                            pass
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
                    self.btn_record.configure(text="● Записать (Пробел / Ctrl+Alt+Space)")
                    self.level["value"] = 0
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
        target = self.paste_target or self.last_external
        current = USER32.GetForegroundWindow()
        # переключились, пока говорили — вставляем туда, где пользователь сейчас
        if current and current != self.hwnd and current != target and USER32.IsWindow(current):
            trace(f"paste: окно сменилось, беру текущее {current}")
            target = current
        if not target or not USER32.IsWindow(target):
            self.status.set("Активное окно не запомнено — текст в буфере обмена")
            self.events.put(("overlay", ("Скопировано в буфер", "#4caf50", 1500)))
            return
        if target == self.hwnd:
            # никогда не вставляем текст сами себе: это и был тот самый случай
            self.status.set("Фокус в окне приложения — текст в буфере (Ctrl+V)")
            self.events.put(("overlay", ("Текст в буфере", "#4caf50", 1500)))
            return
        if USER32.GetForegroundWindow() not in (self.hwnd, target):
            self.status.set("Вы переключили окно — текст в буфере обмена")
            self.events.put(("overlay", ("Окно сменилось — текст в буфере", "#e0a33a", 2000)))
            return
        # SW_RESTORE разворачивает свёрнутое окно, но заодно снимает maximise —
        # поэтому трогаем окно только если оно действительно свёрнуто
        if USER32.IsIconic(target):
            USER32.ShowWindow(target, 9)
        if not self._focus_window(target):
            self.status.set("Не удалось переключиться в окно — текст в буфере обмена")
            self.events.put(("overlay", ("Не удалось вставить — текст в буфере", "#e05555", 2500)))
            return
        time.sleep(0.05)
        injected = send_ctrl_v()
        trace(f"paste target={target} '{self._window_title(target)}' injected={injected}")
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
    root = tk.Tk()
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
