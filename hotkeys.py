"""Разбор строк вида «ctrl+alt+space» в коды WinAPI."""

from __future__ import annotations

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

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


def key_name(vk: int) -> str | None:
    """Имя клавиши по виртуальному коду или None, если не знаем такую."""
    return VK_NAMES.get(vk)


def parse_hotkey(text: str) -> tuple[int, int]:
    """«ctrl+alt+space» -> (модификаторы, виртуальный код). Ошибка — ValueError."""
    parts = [part.strip().lower() for part in str(text).split("+") if part.strip()]
    if not parts:
        raise ValueError("пустая комбинация")

    # только модификаторы (alt+win, ctrl+alt): RegisterHotKey так не умеет,
    # виртуальный код 0 — сигнал «ловить хуком»
    if all(part in MODIFIERS for part in parts):
        if len(parts) < 2:
            raise ValueError("нужна клавиша, а не один модификатор")
        mask = 0
        for name in parts:
            mask |= MODIFIERS[name]
        return mask | MOD_NOREPEAT, 0

    # одиночная клавиша разрешена (например «ё»), но она перехватывается глобально
    *mods, key_name = parts

    mods_mask = 0
    for name in mods:
        if name not in MODIFIERS:
            raise ValueError(f"неизвестный модификатор: {name}")
        mods_mask |= MODIFIERS[name]

    if key_name in KEYS:
        vk = KEYS[key_name]
    elif len(key_name) == 1 and key_name.isalnum():
        vk = ord(key_name.upper())
    else:
        raise ValueError(f"неизвестная клавиша: {key_name}")

    return mods_mask | MOD_NOREPEAT, vk
