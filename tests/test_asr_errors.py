"""Понятные ошибки загрузки torch: чаще всего не хватает Visual C++ Runtime."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asr_engine  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(title: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{title}: {detail}")

    missing_dll = OSError(
        "[WinError 126] Не найден указанный модуль. Error loading "
        r'"C:\Users\User\AppData\Local\Programs\Voice2Text\_internal\torch\lib\shm.dll" '
        "or one of its dependencies.")
    text = asr_engine.describe_import_error(missing_dll)
    check("VC++ подсказка", "Visual C++" in text, text)
    check("ссылка на установщик", asr_engine.VC_REDIST_URL in text, text)
    check("перезапуск", "откройте" in text.lower(), text)

    poisoned = ImportError("`torch` can only be initialized once per process")
    text = asr_engine.describe_import_error(poisoned)
    check("повторный импорт", "закройте" in text.lower(), text)

    other = ImportError("какая-то другая беда")
    text = asr_engine.describe_import_error(other)
    check("прочее", "какая-то другая беда" in text, text)

    # первая причина запоминается: повторный вызов не пытается импортировать снова
    engine = asr_engine.GigaAMEngine(log=lambda _m: None)
    engine._load_failure = "нет Visual C++"
    try:
        engine.load()
    except RuntimeError as exc:
        check("запомненная причина", "нет Visual C++" in str(exc), str(exc))
    else:
        failures.append("запомненная причина: ожидалась RuntimeError")

    for line in failures:
        print("ОШИБКА", line)
    print("ошибки загрузки torch: " + ("ок" if not failures else "есть ошибки"))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
