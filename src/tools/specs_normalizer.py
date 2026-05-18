"""
Specs normalizer.

Mapping синонимов из живых WB-карточек → канонический ключ.

В Phase 5 мы расширим это RAG-доступом к knowledge_base/specs_taxonomy/, где
каждая запись будет векторизована и нормализация станет нечёткой (через
ближайшего соседа). Сейчас — embedded словарь, точное совпадение по lower().

Этот же словарь будет дублироваться в `knowledge_base/specs_taxonomy/*.md` для
KB-загрузки. Источник истины — здесь, KB-файлы генерятся скриптом.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# Canonical key → list of synonyms (lowercase). Все категории в одном словаре —
# имена ключей уникальны между категориями (power_w есть только у фенов и т.п.).
TAXONOMY: dict[str, list[str]] = {
    # --- общие / зубные щётки ---
    "battery_life": [
        "время работы",
        "автономность",
        "автономность работы",
        "продолжительность работы",
        "время автономной работы",
    ],
    "movements": [
        "частота движений",
        "колебаний в минуту",
        "пульсаций в минуту",
        "движений в минуту",
    ],
    "modes_brush": [
        "режимов чистки",
        "количество режимов",
        "режимы работы",
        "режимы чистки",
    ],
    "heads_included": [
        "насадок в комплекте",
        "сменных насадок",
        "комплект насадок",
    ],
    "waterproof": [
        "защита от воды",
        "класс водозащиты",
        "влагозащита",
        "водозащита",
    ],
    "warranty": [
        "гарантия",
        "срок гарантии",
    ],
    "timer": [
        "таймер",
        "smart-таймер",
        "встроенный таймер",
    ],
    # --- фены ---
    "power_w": [
        "мощность",
        "потребляемая мощность",
    ],
    "modes_temp": [
        "температурных режимов",
        "режимов нагрева",
        "режимы температуры",
    ],
    "modes_speed": [
        "скоростей",
        "режимов скорости",
    ],
    "attachments": [
        "сменных насадок",
        "комплект насадок",
        # «насадок в комплекте» уже в heads_included — для фенов оставляем
        # одну версию, чтобы не пересекалось со щётками. Канонические ключи
        # модель не путает: heads_included относится к щёткам, attachments — к фенам.
    ],
    "ionization": [
        "ионизация",
        "функция ионизации",
        "ионный обдув",
    ],
    "cool_shot": [
        "холодный обдув",
        "режим cool shot",
        "холодный воздух",
    ],
    "motor_type": [
        "тип двигателя",
        "мотор",
        "двигатель",
    ],
    "weight_g": [
        "вес",
        "масса",
    ],
    "cord_m": [
        "длина шнура",
        "длина кабеля",
    ],
    # --- эпиляторы ---
    "tweezers": [
        "количество пинцетов",
        "пинцетов",
        "число пинцетов",
    ],
    "wet_dry": [
        "влажная и сухая эпиляция",
        "wet&dry",
        "влажная/сухая эпиляция",
    ],
    "light": [
        "подсветка",
        "встроенная подсветка",
    ],
    "power_type": [
        "тип питания",
        "питание",
    ],
}

# Обратный индекс: synonym (lower) → canonical key.
_REVERSE: dict[str, str] = {}
for canonical, synonyms in TAXONOMY.items():
    for syn in synonyms:
        _REVERSE[syn.lower().strip()] = canonical

# Удобные display-имена для отчётов
DISPLAY_NAMES: dict[str, str] = {
    "battery_life": "Автономность",
    "movements": "Частота движений",
    "modes_brush": "Режимы чистки",
    "heads_included": "Насадок в комплекте",
    "waterproof": "Водозащита",
    "warranty": "Гарантия",
    "timer": "Таймер",
    "power_w": "Мощность",
    "modes_temp": "Температурных режимов",
    "modes_speed": "Скоростей",
    "attachments": "Сменных насадок",
    "ionization": "Ионизация",
    "cool_shot": "Холодный обдув",
    "motor_type": "Тип двигателя",
    "weight_g": "Вес",
    "cord_m": "Длина шнура",
    "tweezers": "Количество пинцетов",
    "wet_dry": "Влажная эпиляция (wet&dry)",
    "light": "Подсветка",
    "power_type": "Тип питания",
}


def normalize_key(raw_key: str) -> str | None:
    """
    Превращает ключ из живой карточки в канонический.
    Возвращает None, если не нашли — тогда характеристика помечается как «не
    нормализована» и не идёт в частотную таблицу.
    """
    return _REVERSE.get(raw_key.lower().strip())


def display(canonical_key: str) -> str:
    return DISPLAY_NAMES.get(canonical_key, canonical_key)


def normalize_specs(raw_specs: dict[str, Any]) -> dict[str, Any]:
    """
    Применяет normalize_key ко всему dict-у specs.
    Если два разных ключа сводятся к одному canonical — оставляем первое
    встретившееся значение (детерминированно).
    """
    out: dict[str, Any] = {}
    for k, v in raw_specs.items():
        canonical = normalize_key(k)
        if canonical and canonical not in out:
            out[canonical] = v
    return out


def frequency_table(
    products_specs: list[dict[str, Any]], top_n: int
) -> dict[str, dict[str, Any]]:
    """
    Считает частоту каждого канонического ключа по выборке.

    Возвращает:
        {
          canonical_key: {
            "frequency": int,
            "typical_values": [str, str, str],
            "display_name": "...",
          },
          ...
        }
    """
    normalized = [normalize_specs(s) for s in products_specs]
    keys_seen: Counter[str] = Counter()
    values_by_key: dict[str, Counter[str]] = {}

    for spec in normalized:
        for k, v in spec.items():
            keys_seen[k] += 1
            values_by_key.setdefault(k, Counter())[str(v)] += 1

    return {
        k: {
            "frequency": cnt,
            "top_n": top_n,
            "typical_values": [val for val, _ in values_by_key[k].most_common(3)],
            "display_name": display(k),
        }
        for k, cnt in keys_seen.most_common()
    }
