"""Разбор комбинаций, коды WinAPI и состояние хоткеев в одном месте.

Здесь же живёт единственный источник правды о том, что запрошено в настройках,
что реально зарегистрировано и какие клавиши надо держать для режима «работает,
пока держу». Раньше это были четыре глобальных словаря в app.py, и любая правка
хоткеев требовала менять их согласованно в трёх местах.
"""

from __future__ import annotations

import ctypes
import threading

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10

PROBE_ID = 0x7F01

MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN,
}

KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "ё": 0xC0, "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}
for _i in range(1, 13):
    KEYS[f"f{_i}"] = 0x6F + _i

# обратное соответствие: виртуальный код -> имя клавиши (для записи комбинации хуком)
VK_NAMES: dict[int, str] = {}
for _name, _vk in KEYS.items():
    VK_NAMES.setdefault(_vk, _name)
for _vk in range(0x30, 0x3A):          # 0..9
    VK_NAMES.setdefault(_vk, chr(_vk))
for _vk in range(0x41, 0x5B):          # A..Z
    VK_NAMES.setdefault(_vk, chr(_vk).lower())

# модификатор -> виртуальный код, который надо держать для режима «удерживать»
MOD_VK = ((MOD_CONTROL, VK_CONTROL), (MOD_ALT, VK_MENU), (MOD_SHIFT, VK_SHIFT))

DEFAULT_COMBOS = {"dictate": "ctrl+shift+space", "show": "ctrl+shift+w"}
FALLBACKS = {
    "dictate": ["ctrl+shift+space", "ctrl+shift+f9", "ctrl+alt+d"],
    "show": ["ctrl+shift+w", "ctrl+shift+f10", "ctrl+alt+o"],
}


def key_name(vk: int) -> str | None:
    """Имя клавиши по виртуальному коду или None, если не знаем такую."""
    return VK_NAMES.get(vk)


def parse_hotkey(text: str) -> tuple[int, int]:
    """«ctrl+alt+space» -> (модификаторы, виртуальный код). Ошибка — ValueError."""
    parts = [part.strip().lower() for part in str(text).split("+") if part.strip()]
    if not parts:
        raise ValueError("пустая комбинация")
    if len(parts) < 2:
        raise ValueError("нужна комбинация: модификатор + клавиша")

    # одиночная клавиша запрещена: она перехватывается глобально и перестаёт печататься
    *mods, key = parts

    mods_mask = 0
    for name in mods:
        if name not in MODIFIERS:
            raise ValueError(f"неизвестный модификатор: {name}")
        if name == "win":
            # Win не поддерживаем: Windows отдаёт такие сочетания системе
            raise ValueError("Win не поддерживается, используйте Ctrl, Shift или Alt")
        mods_mask |= MODIFIERS[name]

    if key in KEYS:
        vk = KEYS[key]
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    else:
        raise ValueError(f"неизвестная клавиша: {key}")

    return mods_mask | MOD_NOREPEAT, vk


def combo_keys(text: str) -> list[int]:
    """Клавиши, которые должны быть зажаты, пока работает комбинация.

    Нужно для режима «удерживать»: раньше там были жёстко зашиты Ctrl/Alt/Space,
    из-за чего запись не останавливалась на любом другом хоткее.
    """
    mods, vk = parse_hotkey(text)
    keys = [vk]
    keys += [code for bit, code in MOD_VK if mods & bit]
    return keys


def probe_hotkey(text: str) -> bool:
    """Свободна ли комбинация: регистрирует на мгновение и отпускает.

    Регистрация привязана к потоку вызова; вызывать из потока слушателя или
    из Tk-потока — оба имеют цикл сообщений.
    """
    try:
        mods, vk = parse_hotkey(text)
    except ValueError:
        return False
    user32 = ctypes.windll.user32
    user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
    try:
        if not user32.RegisterHotKey(None, PROBE_ID, mods, vk):
            return False
        user32.UnregisterHotKey(None, PROBE_ID)
        return True
    except Exception:
        # проверить не смогли — не мешаем пользователю назначить комбинацию
        return True


class HotkeyRegistry:
    """Комбинации, их коды и фактически зарегистрированный вариант."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requested = dict(DEFAULT_COMBOS)
        self._specs = {name: parse_hotkey(combo) for name, combo in self._requested.items()}
        self._actual = dict(self._requested)

    def configure(self, dictate: str, show: str) -> bool:
        """Применяет комбинации из настроек. False — пришлось взять запасную."""
        replaced = False
        with self._lock:
            for name, combo in (("dictate", dictate), ("show", show)):
                try:
                    spec = parse_hotkey(combo)
                except ValueError:
                    combo = FALLBACKS[name][0]
                    spec = parse_hotkey(combo)
                    replaced = True
                self._requested[name] = combo
                self._specs[name] = spec
        return not replaced

    def requested(self, name: str) -> str:
        with self._lock:
            return self._requested[name]

    def actual(self, name: str) -> str:
        with self._lock:
            return self._actual[name]

    def set_actual(self, name: str, combo: str) -> None:
        with self._lock:
            self._actual[name] = combo

    def combos(self) -> dict[str, str]:
        with self._lock:
            return dict(self._requested)

    def actual_all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._actual)

    def spec(self, name: str) -> tuple[int, int]:
        with self._lock:
            return self._specs[name]

    def candidates(self, name: str) -> list[str]:
        """Запрошенная комбинация и запасные к ней."""
        requested = self.requested(name)
        return [requested] + [combo for combo in FALLBACKS[name] if combo != requested]

    def keys(self, name: str) -> list[int]:
        """Клавиши фактически работающей комбинации (для режима «удерживать»)."""
        return combo_keys(self.shown(name))

    def shown(self, name: str) -> str:
        """Комбинация для интерфейса: фактическая, а если не назначена — запрошенная."""
        return self.actual(name) or self.requested(name)


HOTKEYS = HotkeyRegistry()
