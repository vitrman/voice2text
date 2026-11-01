"""Настройки и состояние хоткеев: атомарное сохранение, восстановление, ключи."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as config_module  # noqa: E402
from config import Config  # noqa: E402
from hotkeys import HotkeyRegistry, combo_keys  # noqa: E402


def fresh(name: str) -> Path:
    folder = Path(tempfile.gettempdir()) / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    return folder / "config.json"


def test_roundtrip() -> None:
    path = fresh("cfg-roundtrip")
    settings = Config()
    settings["hotkey"] = "alt+f9"
    settings["device"] = {"name": "Микрофон (USB)", "hostapi": 0}
    settings["replacements"] = {"гига": "GigaAM"}
    settings.save(path)
    settings.save(path)          # второй раз появляется config.bak
    assert not path.with_suffix(".json.tmp").exists(), "остался временный файл"

    loaded = Config.load(path)
    assert loaded["hotkey"] == "alt+f9"
    assert loaded["device"] == {"name": "Микрофон (USB)", "hostapi": 0}
    assert loaded["replacements"] == {"гига": "GigaAM"}
    assert path.with_suffix(".json.bak").exists(), "резервная копия не создана"
    print("сохранение и чтение настроек: ок")


def test_broken_primary_restores_backup() -> None:
    path = fresh("cfg-broken")
    settings = Config()
    settings["hotkey"] = "ctrl+alt+d"
    settings.save(path)          # создаёт config.json и config.bak
    settings.save(path)
    path.write_text('{"hotkey": "ctr', encoding="utf-8")     # обрезанный файл

    errors: list[str] = []
    loaded = Config.load(path, on_error=errors.append)
    assert loaded["hotkey"] == "ctrl+alt+d", loaded["hotkey"]
    assert errors, "ошибка чтения не сообщена"
    print("восстановление из config.bak: ок")


def test_both_broken_gives_defaults() -> None:
    path = fresh("cfg-both")
    Config().save(path)
    path.write_text("мусор", encoding="utf-8")
    path.with_suffix(".json.bak").write_text("тоже мусор", encoding="utf-8")
    errors: list[str] = []
    loaded = Config.load(path, on_error=errors.append)
    assert loaded["hotkey"] == config_module.DEFAULTS["hotkey"]
    assert len(errors) >= 2, errors
    print("оба файла битые — умолчания: ок")


def test_legacy_model_migrated() -> None:
    path = fresh("cfg-model")
    path.write_text(json.dumps({"model": "v3_rnnt"}), encoding="utf-8")
    loaded = Config.load(path)
    assert loaded["model"] == "v3_e2e_rnnt", loaded["model"]
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == "v3_e2e_rnnt"
    print("миграция не-e2e модели: ок")


def test_registry() -> None:
    registry = HotkeyRegistry()
    assert combo_keys("ctrl+shift+space") == [0x20, 0x11, 0x10]
    assert combo_keys("alt+f9") == [0x78, 0x12]

    assert registry.configure("ctrl+alt+d", "ctrl+shift+w") is True
    assert registry.requested("dictate") == "ctrl+alt+d"
    assert registry.actual("dictate") == "ctrl+shift+space"     # ещё не зарегистрировано
    registry.set_actual("dictate", "ctrl+shift+f9")
    assert registry.keys("dictate") == combo_keys("ctrl+shift+f9")
    assert registry.shown("dictate") == "ctrl+shift+f9"

    assert "ctrl+shift+f9" in registry.candidates("dictate")
    assert registry.candidates("dictate")[0] == "ctrl+alt+d"

    # некорректная комбинация заменяется запасной, а не ломает запуск
    assert registry.configure("одна_клавиша", "ctrl+shift+w") is False
    assert registry.requested("dictate") == "ctrl+shift+space"
    print("состояние хоткеев: ок")


if __name__ == "__main__":
    test_roundtrip()
    test_broken_primary_restores_backup()
    test_both_broken_gives_defaults()
    test_legacy_model_migrated()
    test_registry()
    print("все проверки настроек и хоткеев прошли")
