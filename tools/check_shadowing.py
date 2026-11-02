"""Ищет перекрытие методов атрибутами: self.x = … при наличии def x(self)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def check(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    problems = 0
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
        clash = sorted(methods & attrs)
        for name in clash:
            print(f"{path.name}:{node.name}: атрибут self.{name} перекрывает метод")
            problems += 1
    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    total = 0
    for name in ("app.py", "first_run.py", "mic_check.py", "recorder.py", "jobs.py"):
        total += check(root / name)
    print("перекрытий:", total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
