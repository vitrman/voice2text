"""Запись комбинации клавиш клавиатурным хуком: Tk не видит Win.

Состояние живёт в отдельном модуле, потому что `import app` из GUI создаёт
вторую копию модуля (в скрипте он `__main__`) и запись «терялась».
"""

from __future__ import annotations

from hotkeys import key_name

WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
VK_CONTROL, VK_MENU, VK_SHIFT = 0x11, 0x12, 0x10
VK_LWIN, VK_RWIN, VK_ESCAPE = 0x5B, 0x5C, 0x1B

CAP_MODS = {VK_CONTROL: "ctrl", VK_MENU: "alt", VK_SHIFT: "shift",
            VK_LWIN: "win", VK_RWIN: "win"}

STATE = {"active": False, "order": [], "held": set(), "result": None}
HOOK_READY = {"ok": False}


def is_active() -> bool:
    return STATE["active"]


def start_capture() -> None:
    STATE.update(active=True, order=[], held=set(), result=None)


def cancel_capture() -> None:
    STATE.update(active=False, order=[], held=set(), result=None)


def poll_capture() -> str | None:
    """Готовая комбинация ('' — отмена) или None, пока ждём."""
    if STATE["result"] is None:
        return None
    result = STATE["result"]
    cancel_capture()
    return result


def feed_key(vk: int, w_param: int) -> None:
    """Вызывается хуком до окон; vk — виртуальный код, w_param — сообщение."""
    if not STATE["active"]:
        return
    down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
    mod = CAP_MODS.get(vk)
    if mod:
        if down:
            if mod not in STATE["held"]:
                STATE["held"].add(mod)
                STATE["order"].append(mod)
        else:
            STATE["held"].discard(mod)
            if not STATE["held"] and STATE["result"] is None:
                order = STATE["order"]
                if order and order != ["win"]:      # одна Win — не комбинация
                    STATE["result"] = "+".join(order)
        return
    if down:
        if vk == VK_ESCAPE:
            STATE["result"] = ""
            return
        name = key_name(vk)
        if name:
            STATE["result"] = "+".join(STATE["order"] + [name])
