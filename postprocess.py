"""Постобработка текста: словарь замен и мелочи форматирования."""

from __future__ import annotations

import re


def apply_replacements(text: str, replacements: dict) -> str:
    """Заменяет термины целиком по границам слов, без учёта регистра."""
    if not text or not replacements:
        return text
    for source, target in sorted(replacements.items(), key=lambda item: -len(item[0])):
        if not source:
            continue
        pattern = rf"(?<!\w){re.escape(source)}(?!\w)"
        text = re.sub(pattern, target, text, flags=re.IGNORECASE)
    return text


def tidy(text: str) -> str:
    """Убирает двойные пробелы и пробелы перед знаками препинания."""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text
