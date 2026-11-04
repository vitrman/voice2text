"""Проверки для CI: без torch, без модели, без GUI."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hotkeys  # noqa: E402
import update  # noqa: E402
import version  # noqa: E402


def main() -> int:
    assert re.fullmatch(r"\d+\.\d+\.\d+\.1", version.VERSION), version.VERSION

    assert update.version_tuple("v0.7.3.1") == (0, 7, 3, 1)
    assert update.version_tuple("0.8") == (0, 8, 0, 0)
    assert update.version_tuple("0.7.3.1") < update.version_tuple("0.8.0.1")

    assert hotkeys.parse_hotkey("ctrl+shift+space") == (16390, 32)
    assert hotkeys.parse_hotkey("alt+f9") == (16385, 120)

    for bad in ("win+d", "ctrl+alt", "ё", ""):
        try:
            hotkeys.parse_hotkey(bad)
        except ValueError:
            continue
        raise AssertionError(f"должно быть отклонено: {bad!r}")

    choices = hotkeys.hotkey_choices()
    assert choices, "список комбинаций для меню пуст"
    assert len(choices) == len(set(choices)), "в списке комбинаций есть дубликаты"
    for combo in choices:
        assert "win" not in combo.lower(), combo
        hotkeys.parse_hotkey(combo)          # каждая комбинация из меню разбирается
    for name, default in hotkeys.DEFAULT_COMBOS.items():
        assert default in choices, f"умолчание {default} отсутствует в списке меню"

    # окно и созвон можно отключить: пустая комбинация — это значение, а не ошибка
    assert hotkeys.is_disabled("") and hotkeys.is_disabled(hotkeys.DISABLED_LABEL)
    assert not hotkeys.is_disabled("ctrl+shift+w")
    assert hotkeys.normalize(hotkeys.DISABLED_LABEL) == ""

    text = (ROOT / "version.py").read_text(encoding="utf-8")
    assert f'"{version.VERSION}"' in text

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert version.VERSION in changelog, "нет записи о текущей версии в CHANGELOG.md"

    # powershell.exe (5.1) без BOM читает .ps1 как ANSI: русский текст ломается,
    # а скрипт публикации перестаёт разбираться синтаксически
    script = ROOT / "tools" / "publish_release.ps1"
    assert script.read_bytes().startswith(b"\xef\xbb\xbf"), (
        "tools/publish_release.ps1 должен быть в UTF-8 с BOM")

    # атрибут вида self.capture = None перекрывает метод capture: именно так запись
    # перестала запускаться в 0.9.0.1 — поток стартовал с target=None
    for module in ("app.py", "first_run.py", "mic_check.py", "recorder.py", "jobs.py"):
        clashes = shadowed_methods(ROOT / module)
        assert not clashes, f"{module}: атрибуты перекрывают методы: {clashes}"

    print(f"ok: {version.VERSION}")
    return 0


def shadowed_methods(path: pathlib.Path) -> list[str]:
    """Имена методов класса, которые перекрываются атрибутами self.<имя>."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
        attrs = set()
        for item in ast.walk(node):
            if isinstance(item, ast.Attribute) and isinstance(item.ctx, ast.Store):
                base = item.value
                if isinstance(base, ast.Name) and base.id == "self":
                    attrs.add(item.attr)
        result += [f"{node.name}.{name}" for name in sorted(methods & attrs)]
    return result


if __name__ == "__main__":
    sys.exit(main())
