"""Настройки приложения: config.json рядом с программой."""

from __future__ import annotations

import json
from pathlib import Path

from paths import DATA, ROOT

from hotkeys import parse_hotkey

CONFIG_PATH = DATA / "config.json"

DEFAULTS = {
    "model": "v3_e2e_rnnt",
    "device": None,            # индекс устройства ввода или null = по умолчанию
    "autopaste": True,
    "hotkey": "ctrl+shift+space",
    "show_hotkey": "ctrl+shift+w",
    "hotkey_mode": "toggle",   # toggle = нажал/нажал, hold = держать
    "silence_ms": 700,
    "auto_stop": True,         # False — писать до кнопки «Стоп»/хоткея, паузы не режут
    "max_seconds": 120,
    "chunk_seconds": 20,
    "replacements": {},        # {"как слышится": "Как надо"}
    "start_minimized": True,
}


class Config(dict):
    """dict с дефолтами и сохранением в файл."""

    def __init__(self, data: dict | None = None):
        super().__init__(DEFAULTS)
        if data:
            self.update({key: value for key, value in data.items() if key in DEFAULTS})
        if not isinstance(self.get("replacements"), dict):
            self["replacements"] = {}

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        try:
            with open(path, encoding="utf-8") as fh:
                config = cls(json.load(fh))
        except (OSError, json.JSONDecodeError):
            return cls()

        # старые умолчания с Alt снимали фокус при отпускании — переносим один раз
        changed = False
        if config.get("hotkey") == "ctrl+alt+space":
            config["hotkey"] = "ctrl+shift+space"
            changed = True
        if config.get("show_hotkey") == "ctrl+alt+w":
            config["show_hotkey"] = "ctrl+shift+w"
            changed = True
        # одиночные клавиши и мусор больше не поддерживаются: только комбинации
        for key in ("hotkey", "show_hotkey"):
            try:
                parse_hotkey(config.get(key) or "")
            except ValueError:
                config[key] = DEFAULTS[key]
                changed = True
        if changed:
            try:
                config.save(path)
            except OSError:
                pass
        return config

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.write_text(json.dumps(self, ensure_ascii=False, indent=2), encoding="utf-8")
