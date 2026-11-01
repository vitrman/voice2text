"""Автозапуск и вердикт проверки микрофона — без GUI и без записи в реестр."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import autostart  # noqa: E402
from mic_check import verdict_for  # noqa: E402


def test_autostart_from_sources() -> None:
    # из исходников автозапуск недоступен: в реестр попал бы путь к python.exe
    assert autostart.available() is False
    assert autostart.command() == ""
    assert autostart.sync() is False
    try:
        autostart.set_enabled(True)
    except OSError as exc:
        assert "установленной" in str(exc), exc
    else:
        raise AssertionError("из исходников автозапуск включаться не должен")
    print("автозапуск: из исходников недоступен")


def test_verdicts() -> None:
    silent, silent_color = verdict_for(-120)
    assert "Нет сигнала" in silent, silent
    quiet, _ = verdict_for(-50)
    assert "Тихо" in quiet, quiet
    mild, _ = verdict_for(-45)
    assert "Тиховато" in mild, mild
    good, good_color = verdict_for(-25)
    assert "Слышно хорошо" in good, good
    loud, _ = verdict_for(-3)
    assert "громко" in loud, loud
    assert good_color != silent_color
    print("вердикты проверки микрофона: ок")


if __name__ == "__main__":
    test_autostart_from_sources()
    test_verdicts()
    print("все проверки запуска и микрофона прошли")
