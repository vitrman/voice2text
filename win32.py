"""Win32-обвязка: ввод, фокус, окна, хук, одиночный экземпляр.

Вынесено из app.py: окно и логика не должны тонуть в ctypes-структурах, иначе
любая правка вставки или хоткея задевает интерфейс.
"""

from __future__ import annotations

import ctypes
import threading

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
WM_HOTKEY = 0x0312
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
WH_KEYBOARD_LL = 13

# 64-битные дескрипторы окон: без argtypes ctypes обрезает их до 32 бит и вызовы
# молча ничего не делают (окно «показывается», но остаётся на месте)
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetAncestor.restype = ctypes.c_void_p
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = ctypes.c_bool
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool, ctypes.c_wchar_p]
kernel32.OpenEventW.restype = ctypes.c_void_p
kernel32.OpenEventW.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_wchar_p]
kernel32.SetEvent.argtypes = [ctypes.c_void_p]
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


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


user32.SendInput.restype = ctypes.c_uint
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint), ("wParam", ctypes.c_size_t),
                ("lParam", ctypes.c_ssize_t), ("time", ctypes.c_ulong), ("pt", POINT)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_ulong), ("scanCode", ctypes.c_ulong),
                ("flags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)


def get_foreground():
    return user32.GetForegroundWindow()


def is_window(hwnd) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_iconic(hwnd) -> bool:
    return bool(user32.IsIconic(hwnd))


def show_window(hwnd, command: int = 9) -> None:
    user32.ShowWindow(hwnd, command)


def window_title(hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buffer, 256)
    return buffer.value


def force_foreground(hwnd) -> bool:
    """Поднимает окно, обходя запрет смены фокуса через AttachThreadInput."""
    user32.BringWindowToTop(hwnd)
    if user32.SetForegroundWindow(hwnd):
        return True
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return False
    target_thread = user32.GetWindowThreadProcessId(foreground, None)
    current_thread = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(current_thread, target_thread, True)
    try:
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        user32.AttachThreadInput(current_thread, target_thread, False)


def focus_window(hwnd, attempts: int = 3, pause: float = 0.12) -> bool:
    """Переключает фокус и проверяет, что он действительно у цели."""
    import time

    for _ in range(attempts):
        if user32.GetForegroundWindow() == hwnd:
            return True
        force_foreground(hwnd)
        time.sleep(pause)
    return user32.GetForegroundWindow() == hwnd


def send_ctrl_v(log=None) -> bool:
    """Отправляет Ctrl+V в активное окно. False — если система заблокировала ввод."""
    events = [INPUT(type=1, u=_InputUnion(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))
              for vk, flags in ((VK_CONTROL, 0), (VK_V, 0), (VK_V, KEYEVENTF_KEYUP),
                                (VK_CONTROL, KEYEVENTF_KEYUP))]
    array = (INPUT * len(events))(*events)
    kernel32.SetLastError(0)
    sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent == len(events):
        return True

    error = kernel32.GetLastError()
    if log is not None:
        log(f"SendInput: {sent}/{len(events)}, ошибка {error}, sizeof(INPUT)={ctypes.sizeof(INPUT)}")
    # 5 — UIPI (окно с большими правами), 87 — неверная структура, 0 — ввод перехвачен
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    return False


def make_overlay(hwnd) -> None:
    """Оверлей не должен забирать фокус и попадать в Alt+Tab."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def place_window(hwnd, x: int, y: int, width: int, height: int) -> None:
    user32.SetWindowPos(hwnd, -1, x, y, width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW)


_HOOK_PROC = None


def start_keyboard_hook(proc, log=None, on_ready=None) -> None:
    """Ставит низкоуровневый хук в отдельном потоке с циклом сообщений.

    proc — HOOKPROC-совместимая функция (n_code, w_param, l_param) -> bool/int.
    """
    global _HOOK_PROC
    _HOOK_PROC = HOOKPROC(proc)

    def run() -> None:
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _HOOK_PROC, None, 0)
        if not handle:
            if log is not None:
                log(f"hook: не установлен, ошибка {kernel32.GetLastError()}")
            return
        if log is not None:
            log("hook: клавиатурный хук установлен")
        if on_ready is not None:
            on_ready()
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass

    threading.Thread(target=run, daemon=True).start()


SINGLE_INSTANCE_HANDLE = None
SHOW_EVENT = {"handle": None, "callback": None}
SHOW_EVENT_NAME = "Local\\voice2text-show"
EVENT_MODIFY_STATE = 0x0002
WAIT_INFINITE = 0xFFFFFFFF


def already_running(allow_multi: bool = False) -> bool:
    """Один экземпляр: иначе два окна делят хоткей, лог и файлы модели."""
    if allow_multi:
        return False
    global SINGLE_INSTANCE_HANDLE
    SINGLE_INSTANCE_HANDLE = kernel32.CreateMutexW(None, False, "Local\\voice2text-single")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def start_show_listener(callback) -> None:
    """Первый экземпляр слушает событие: второй запуск просит показать окно."""
    SHOW_EVENT["callback"] = callback
    SHOW_EVENT["handle"] = kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)

    def wait() -> None:
        handle = SHOW_EVENT["handle"]
        if not handle:
            return
        while True:
            if kernel32.WaitForSingleObject(handle, WAIT_INFINITE) != 0:
                return
            if SHOW_EVENT["callback"] is not None:
                SHOW_EVENT["callback"]()

    threading.Thread(target=wait, daemon=True).start()


def request_show() -> bool:
    """Второй экземпляр просит первый показать окно. False — если он старый."""
    handle = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, SHOW_EVENT_NAME)
    if not handle:
        return False
    try:
        kernel32.SetEvent(handle)
    finally:
        kernel32.CloseHandle(handle)
    return True
