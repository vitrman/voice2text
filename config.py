"""Настройки приложения: config.json рядом с программой.

Сохранение атомарное (временный файл + os.replace) и с резервной копией:
раньше обрыв записи оставлял пустой config.json, а загрузка молча брала
умолчания — пользователь терял хоткеи, словарь замен и выбранный микрофон.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from paths import DATA

from hotkeys import parse_hotkey

CONFIG_PATH = DATA / "config.json"

# в интерфейсе только e2e-модели: они сами ставят пунктуацию и регистр,
# не-e2e давали сплошной строчный текст и выглядели как «хуже распознаёт»
MODELS = ("v3_e2e_rnnt", "v3_e2e_ctc")
MODEL_LABELS = {
    "v3_e2e_rnnt": "GigaAM v3 — точнее",
    "v3_e2e_ctc": "GigaAM v3 CTC — быстрее",
}
LEGACY_MODELS = ("v3_rnnt", "v3_ctc", "rnnt", "ctc")

DEFAULTS = {
    "model": "v3_e2e_rnnt",
    "device": None,            # {"name": ..., "hostapi": ...} или null = по умолчанию
    "autopaste": True,
    "hotkey": "ctrl+shift+space",
    "show_hotkey": "ctrl+shift+w",
    "hotkey_mode": "toggle",   # toggle = нажал/нажал, hold = держать
    "silence_ms": 700,
    "auto_stop": True,         # False — писать до кнопки «Стоп»/хоткея, паузы не режут
    "chunk_seconds": 20,
    "replacements": {},        # {"как слышится": "Как надо"}
    "start_minimized": True,
    "check_updates": True,     # искать новую версию на GitHub при запуске
    "github_repo": "vitrman/voice2text",
    "last_update_check": 0,    # unix-время последней автопроверки
    "first_run_done": False,   # визард показывается один раз
}


def _backup_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".bak")


class Config(dict):
    """dict с дефолтами и сохранением в файл."""

    def __init__(self, data: dict | None = None):
        super().__init__(DEFAULTS)
        if data:
            self.update({key: value for key, value in data.items() if key in DEFAULTS})
        if not isinstance(self.get("replacements"), dict):
            self["replacements"] = {}

    @classmethod
    def load(cls, path: Path = CONFIG_PATH, on_error=None) -> "Config":
        """Читает настройки: основной файл, затем резервная копия, затем умолчания."""
        data = None
        for candidate, label in ((path, "config.json"), (_backup_path(path), "config.bak")):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                if on_error is not None and candidate.exists():
                    on_error(f"настройки: {label} не прочитан ({exc.__class__.__name__})")
                continue
            if isinstance(data, dict):
                break
            data = None
        config = cls(data)

        changed = False
        # старые умолчания с Alt снимали фокус при отпускании — переносим один раз
        if config.get("hotkey") == "ctrl+alt+space":
            config["hotkey"] = DEFAULTS["hotkey"]
            changed = True
        if config.get("show_hotkey") == "ctrl+alt+w":
            config["show_hotkey"] = DEFAULTS["show_hotkey"]
            changed = True
        # одиночные клавиши и мусор больше не поддерживаются: только комбинации
        for key in ("hotkey", "show_hotkey"):
            try:
                parse_hotkey(config.get(key) or "")
            except ValueError:
                config[key] = DEFAULTS[key]
                changed = True
        # не-e2e модели убраны из интерфейса: без пунктуации текст выглядел сломанным
        if config.get("model") not in MODELS:
            config["model"] = DEFAULTS["model"]
            changed = True
        if changed:
            try:
                config.save(path)
            except OSError:
                pass
        return config

    def save(self, path: Path = CONFIG_PATH) -> None:
        """Пишет через временный файл: обрыв не может испортить настройки."""
        payload = json.dumps(self, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        if path.exists():
            try:
                shutil.copyfile(path, _backup_path(path))
            except OSError:
                pass
        os.replace(tmp, path)
