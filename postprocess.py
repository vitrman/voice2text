"""Постобработка текста: словарь замен, слова-паразиты, мелочи форматирования."""

from __future__ import annotations

import re

VOWELS = "аэыоуиё"

# Гласные-заполнители: «э-э-э», «а-а-а»
_HYPHEN_VOWELS = re.compile(rf"(?<!\w)(?:[{VOWELS}]-){{2,}}[{VOWELS}](?!\w)", re.IGNORECASE)
# «ээээ» — растянутая гласная отдельным токеном
_STRETCHED_VOWEL = re.compile(rf"(?<!\w)[{VOWELS}]{{3,}}(?!\w)", re.IGNORECASE)
# «э э э» — три и более одиночных гласных подряд
_SPACED_VOWELS = re.compile(rf"(?<!\w)(?:[{VOWELS}]\s+){{2,}}[{VOWELS}](?!\w)", re.IGNORECASE)
# «ммм», «н-н-н»
_NASAL = re.compile(r"(?<!\w)(?:м{3,}|н{3,}|(?:м-){2,}м|(?:н-){2,}н)(?!\w)", re.IGNORECASE)
# «да-да-да» — слово, повторённое через дефис три и более раз
_WORD_HYPHEN_RUN = re.compile(rf"(?<!\w)([а-яё]{{2,}})(?:-\1){{2,}}(?!\w)", re.IGNORECASE)
# «ну-у», «да-а» — растянутое окончание настоящего слова: сжимаем, а не удаляем
_WORD_STRETCH = re.compile(rf"(?<!\w)([а-яё]{{2,}})-([{VOWELS}])\b", re.IGNORECASE)


def _cleanup(text: str, capitalize: bool = False) -> str:
    """Убирает следы удаления: лишние пробелы, запятые, строчную букву в начале."""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([.!?])\s*,\s*", r"\1 ", text)      # «знаю., типа» -> «знаю. типа»
    text = re.sub(r"(?:\s*,\s*){2,}", ", ", text)
    text = re.sub(r"^[\s,;:—–-]+", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    # удалили заполнитель в начале предложения — следующее слово пишем с заглавной
    text = re.sub(r"([.!?]\s+)([а-яё])",
                  lambda match: match.group(1) + match.group(2).upper(), text)
    if capitalize and text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


def drop_fillers(text: str, log=None) -> str:
    """Убирает звуки-заполнители («э-э-э», «а-а-а», «ммм») из готового текста.

    Удаляются только «чистые» звуки: растянутые гласные и носовые, которые не
    являются словами. Настоящие слова-паразиты («ну», «типа», «значит») остаются:
    вырезать их по списку опасно — «типа данных» и «значит результат» теряют смысл.
    «Ну-у» сжимается до «ну», «да-да-да» — до «да».
    """
    if not text:
        return text

    removed: list[str] = []

    def drop(match: re.Match) -> str:
        token = match.group(0)
        # «ООО «Ромашка»» и прочие короткие аббревиатуры не трогаем
        if token.isupper() and "-" not in token and len(token) <= 3:
            return token
        removed.append(token)
        return ""

    result = text
    for pattern in (_HYPHEN_VOWELS, _NASAL, _SPACED_VOWELS, _STRETCHED_VOWEL):
        result = pattern.sub(drop, result)
    result = _WORD_HYPHEN_RUN.sub(r"\1", result)   # «да-да-да» -> «да»
    result = _WORD_STRETCH.sub(r"\1", result)

    if not removed and result == text:
        return text

    # удалили в начале (осталась запятая/тире) — фразу надо начинать с заглавной
    start_removed = result[:1] and result.lstrip()[:1] in (",", ";", ":", "—", "–", "-")
    capitalize = bool(start_removed) or text[:1].isupper()

    if removed and log is not None:
        sample = ", ".join(f"«{item}»" for item in removed[:5])
        tail = " …" if len(removed) > 5 else ""
        log(f"убрано звуков-заполнителей: {len(removed)} ({sample}{tail})")
    return _cleanup(result, capitalize=capitalize)


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
