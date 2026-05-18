"""
USP classifier.

Гибридный подход:
  1. Lightweight keyword-rules — быстрый бейзлайн, не требует LLM.
  2. LLM (через USP Analyst) — кластеризует то, что rules не разнесли.

Типы УТП:
  - technological — технические фишки, числа, технологии
  - value         — комплектация, гарантия, доставка
  - emotional     — обещания эмоций / результата
  - social        — социальное доказательство

Список keywords ниже сознательно не исчерпывающий — это бейзлайн. LLM закрывает
длинный хвост.
"""

from __future__ import annotations

import re
from collections import Counter

from src.schemas.analysis import UspItem

_TECH_PATTERNS = [
    r"\b\d+\s*(?:вт|ватт|w)\b",
    r"\bтехнолог",
    r"\bионизац",
    r"\bsensitive\b",
    r"\bкерамическ",
    r"\bтурмалин",
    r"\bбесщ[её]точн",
    r"\bsmart[- ]?таймер",
    r"\b\d+\s*режимов?\b",
    r"\bкоэффициент",
]

_VALUE_PATTERNS = [
    r"\bгаранти",
    r"\b\d+\s*(?:года|лет|год)\b.*гаранти",
    r"\bв комплекте\b",
    r"\bкомплект",
    r"\bнасадок\b",
    r"\bдоставк",
    r"\bремонт по гаранти",
    r"\bаккумулятор в комплекте",
]

_EMOTIONAL_PATTERNS = [
    r"\bидеальн",
    r"\bпрофессиональн.+результат",
    r"\bбелоснежн",
    r"\bкак у\b",
    r"\bсалон",
    r"\bтих.+работ",
    r"\bне разбуд",
    r"\bкомфорт",
    r"\bбережн",
]

_SOCIAL_PATTERNS = [
    r"\bхит продаж",
    r"\bтоп[- ]?\d",
    r"\bрекоменд.+стоматолог",
    r"\bвыбор косметолог",
    r"\bтыс.+довольных",
    r"\b\d{2,}\s*\d{3,}\b.*клиент",
    r"\bлидер продаж",
    r"\bбестселлер",
]


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def classify_phrase(phrase: str) -> str | None:
    """
    Возвращает один из {technological, value, emotional, social} или None
    (если ни один rule не сматчился — LLM должен разобрать).
    """
    if _matches_any(phrase, _TECH_PATTERNS):
        return "technological"
    if _matches_any(phrase, _VALUE_PATTERNS):
        return "value"
    if _matches_any(phrase, _EMOTIONAL_PATTERNS):
        return "emotional"
    if _matches_any(phrase, _SOCIAL_PATTERNS):
        return "social"
    return None


def extract_phrases(text: str) -> list[str]:
    """
    Делит маркетинговый текст карточки на отдельные «УТП-куски».
    Разделители на WB: запятые, точки, точки с запятой и переводы строк.
    """
    parts = re.split(r"[.,;\n]+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def baseline_classify(text: str, seller: str | None = None) -> list[UspItem]:
    """
    Прогоняет текст через rules. Только те фразы, что сматчились — попадают
    в результат с типом. Не сматчившиеся возвращаем тоже, но с None —
    LLM их доразложит.
    """
    out: list[UspItem] = []
    for phrase in extract_phrases(text):
        usp_type = classify_phrase(phrase)
        # phrases без типа не игнорируем — отдадим LLM на классификацию
        out.append(
            UspItem(
                seller=seller,
                phrase=phrase,
                usp_type=usp_type or "value",  # дефолт — value (наименее провокационный)
            )
        )
    return out


def type_distribution(items: list[UspItem]) -> dict[str, int]:
    c: Counter[str] = Counter(item.usp_type for item in items)
    # Гарантируем, что все 4 типа представлены — иначе сложно искать gaps
    for t in ("technological", "value", "emotional", "social"):
        c.setdefault(t, 0)
    return dict(c)


def find_gaps(distribution: dict[str, int], threshold_share: float = 0.10) -> list[str]:
    """
    Тип считается gap-ом, если его доля < threshold_share.
    Это и есть «пустоты для отстройки».
    """
    total = sum(distribution.values()) or 1
    return [t for t, n in distribution.items() if n / total < threshold_share]
