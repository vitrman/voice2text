"""Хоткеи по частям: модификатор, второй модификатор и клавиша отдельными списками."""

from __future__ import annotations

import sys
from pathlib import Path

import customtkinter as ctk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
from hotkeys import DISABLED_LABEL  # noqa: E402


def main() -> int:
    app_module.App._setup_tray = lambda self: False    # настоящий трей под песочницей недоступен
    saved: list[dict] = []
    app_module.Config.save = lambda self, path=None: saved.append(dict(self))  # не пишем config.json

    root = ctk.CTk()
    instance = app_module.App(root)

    failures: list[str] = []

    def check(title: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{title}: {detail}")

    # исходное состояние: у диктовки ctrl / shift / space
    parts = tuple(var.get() for var in instance._hotkey_vars["dictate"])
    check("исходная комбинация", parts == ("ctrl", "shift", "space"), str(parts))

    # собираем alt + «нет» + f9
    m1, m2, key = instance._hotkey_vars["dictate"]
    m1.set("alt")
    m2.set(app_module.NO_MODIFIER)
    key.set("f9")
    instance._on_hotkey_change("dictate")
    check("составная комбинация", instance._hotkey_combo("dictate") == "alt+f9",
          instance._hotkey_combo("dictate"))

    # окно отключаем
    show_key = instance._hotkey_vars["show"][2]
    show_key.set(DISABLED_LABEL)
    instance._on_hotkey_change("show")
    check("окно отключено", instance._hotkey_combo("show") == "",
          instance._hotkey_combo("show"))

    # повторяющийся модификатор не принимаем, списки возвращаются к сохранённому
    m1.set("alt")
    instance._on_hotkey_change("dictate")
    m2.set("alt")
    instance._on_hotkey_change("dictate")
    check("дубль модификатора", instance._hotkey_combo("dictate") == "alt+f9",
          instance._hotkey_combo("dictate"))

    # применение: в конфиг ложатся части как одна комбинация, пусто — как «нет хоткея»
    instance._apply_settings()
    check("конфиг диктовки", instance.config["hotkey"] == "alt+f9", instance.config["hotkey"])
    check("конфиг окна", instance.config["show_hotkey"] == "",
          repr(instance.config["show_hotkey"]))

    instance._quit()
    for line in failures:
        print("ОШИБКА", line)
    print("хоткеи по частям: " + ("ок" if not failures else "есть ошибки"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
