"""Проверки для CI: без torch, без модели, без GUI."""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import hotkeys  # noqa: E402
import version  # noqa: E402


def main() -> int:
    assert re.fullmatch(r"\d+\.\d+\.\d+\.1", version.VERSION), version.VERSION

    assert hotkeys.parse_hotkey("ctrl+shift+space") == (16390, 32)
    assert hotkeys.parse_hotkey("alt+f9") == (16385, 120)

    for bad in ("win+d", "ctrl+alt", "ё", ""):
        try:
            hotkeys.parse_hotkey(bad)
        except ValueError:
            continue
        raise AssertionError(f"должно быть отклонено: {bad!r}")

    text = (ROOT / "version.py").read_text(encoding="utf-8")
    assert f'"{version.VERSION}"' in text

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert version.VERSION in changelog, "нет записи о текущей версии в CHANGELOG.md"

    print(f"ok: {version.VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
