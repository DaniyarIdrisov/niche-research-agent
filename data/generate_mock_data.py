"""
Detereministic mock-data generator for Wildberries small-home-appliance niche.

Запуск:
    python data/generate_mock_data.py

Что делает:
- Генерирует ~110 карточек товаров в трёх категориях МБТ:
    * electric_toothbrush (электрические зубные щётки)
    * hair_dryer (фены для волос)
    * epilator (эпиляторы)
- Цены, рейтинги, отзывы — реалистично распределены (лог-нормаль / бета / Парето).
- Названия, описания и УТП — собираются из шаблонов с реальными торговыми формулировками
  с WB (мощность, время работы, ионизация, число насадок и т.п.).
- Характеристики — намеренно с «синонимами» (например «время работы» vs «автономность»),
  чтобы было что нормализовывать в Specs Miner.

Seed зафиксирован — запуск даёт идентичный JSON, его можно коммитить.

Stdlib only — никаких внешних зависимостей.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

SEED = 42
OUTPUT = Path(__file__).with_name("mock_wb_products.json")

# ---------------------------------------------------------------------------
# Общие справочники
# ---------------------------------------------------------------------------

CERTIFICATIONS = ["ЕАЭС", "ТР ТС 004/2011", "ТР ТС 020/2011", "EAC", "Декларация соответствия"]

# Базовые «слоты» для УТП — по типам, чтоб USP Analyst было что классифицировать.
USP_TECH = [
    "звуковая технология",
    "5 режимов чистки",
    "ионизация воздуха",
    "ионная технология ухода",
    "турмалиновое покрытие",
    "бесщёточный мотор",
    "AC-мотор повышенной мощности",
    "керамический нагрев",
    "подсветка проблемных зон",
    "технология sensitive для чувствительной кожи",
    "режим cool shot",
    "smart-таймер 2 минуты",
    "датчик давления",
]
USP_VALUE = [
    "2 года гарантии",
    "3 года гарантии производителя",
    "4 насадки в комплекте",
    "8 насадок в наборе",
    "комплект с дорожным чехлом",
    "запасной аккумулятор в комплекте",
    "доставка из РФ",
    "ремонт по гарантии в 1500 городов",
]
USP_EMO = [
    "профессиональный результат как у парикмахера",
    "идеально гладкая кожа на 4 недели",
    "белоснежная улыбка за 14 дней",
    "уход салонного уровня дома",
    "тихая работа — не разбудит ребёнка",
]
USP_SOCIAL = [
    "хит продаж 2025",
    "рекомендуют стоматологи",
    "выбор косметологов",
    "более 50 000 довольных клиентов",
    "топ-1 в категории",
]

# ---------------------------------------------------------------------------
# Категория 1: электрические зубные щётки
# ---------------------------------------------------------------------------

TOOTHBRUSH_BRANDS = [
    ("Soocas", ["X3U", "X3 Pro", "V2", "X5", "D3"]),
    ("Oral-B", ["Vitality Pro", "Pro 700", "iO Series 3", "Pro 3 3000"]),
    ("Philips Sonicare", ["HX3212", "HX6800", "ProtectiveClean"]),
    ("Xiaomi", ["Mi Electric T100", "Mi Smart T500", "T700"]),
    ("CS Medica", ["CS-484", "CS-562", "CS-888"]),
    ("Revyline", ["RL 010", "RL 015", "RL 060 Pro"]),
    ("Longa Vita", ["UltraMax", "SmartClean", "B95R"]),
    ("Polaris", ["PETB 0701 TC", "PETB 0503"]),
    ("Lebooo", ["Smart Sonic", "Pro Clean"]),
    ("Galaxy", ["GL 4980", "GL 4983"]),
]
TOOTHBRUSH_TYPE = ["звуковая", "ультразвуковая", "вибрационная"]
TOOTHBRUSH_COLORS = ["чёрный", "белый", "розовый", "серый", "голубой", "золотой"]
# Спецы: 2 поля даны с разными формулировками намеренно (для нормализатора).
TOOTHBRUSH_SPECS_KEYS_VARIANTS = {
    "battery_life": ["время работы", "автономность работы", "автономность"],
    "movements": ["частота движений", "колебаний в минуту", "пульсаций в минуту"],
    "modes": ["режимов чистки", "количество режимов", "режимы работы"],
    "heads_included": ["насадок в комплекте", "сменных насадок", "комплект насадок"],
    "waterproof": ["защита от воды", "класс водозащиты", "влагозащита"],
    "warranty": ["гарантия", "срок гарантии"],
    "timer": ["таймер", "smart-таймер", "встроенный таймер"],
}
TOOTHBRUSH_SELLERS = [
    "ИП Иванов А.А.",
    "ООО Бьюти-Маркет",
    "Soocas Official",
    "ООО Здоровая Улыбка",
    "ИП Петров В.С.",
    "ООО Дентал Трейд",
    "ООО Атлант",
]

# ---------------------------------------------------------------------------
# Категория 2: фены для волос
# ---------------------------------------------------------------------------

DRYER_BRANDS = [
    ("Philips", ["BHD300/00", "BHD340/10", "DryCare BHC010"]),
    ("Rowenta", ["CV5930", "CV7820", "Studio Dry"]),
    ("BaByliss", ["6611E", "Pro Light 2000"]),
    ("Polaris", ["PHD 2077Ti", "PHD 2065 Argan"]),
    ("Centek", ["CT-2236", "CT-2241"]),
    ("Galaxy", ["GL 4310", "GL 4341"]),
    ("Scarlett", ["SC-HD70IT09", "SC-HD70I39"]),
    ("BBK", ["BHD3221i"]),
    ("Vitek", ["VT-2293", "VT-8211"]),
    ("Soocas", ["H3S", "H5"]),
    ("Dreame", ["Hairdryer Lite"]),
]
DRYER_COLORS = ["чёрный", "белый", "розовый", "графит", "золотой"]
DRYER_SPECS_KEYS_VARIANTS = {
    "power_w": ["мощность", "потребляемая мощность"],
    "modes_temp": ["температурных режимов", "режимов нагрева", "режимы температуры"],
    "modes_speed": ["скоростей", "режимов скорости"],
    "attachments": ["насадок в комплекте", "сменных насадок", "комплект насадок"],
    "ionization": ["ионизация", "функция ионизации", "ионный обдув"],
    "cool_shot": ["холодный обдув", "режим cool shot", "холодный воздух"],
    "motor": ["тип двигателя", "мотор", "двигатель"],
    "weight_g": ["вес", "масса"],
    "cord_m": ["длина шнура", "длина кабеля"],
    "warranty": ["гарантия", "срок гарантии"],
}
DRYER_SELLERS = [
    "ИП Сидоров К.К.",
    "ООО Бьюти-Маркет",
    "Philips Official",
    "ООО Атлант",
    "ООО Хоум-Стайл",
    "ИП Кузнецова М.А.",
    "ООО Импорт-Тех",
]

# ---------------------------------------------------------------------------
# Категория 3: эпиляторы
# ---------------------------------------------------------------------------

EPILATOR_BRANDS = [
    ("Philips", ["BRE235/00", "BRE275/00", "Satinelle Essential"]),
    ("Braun", ["Silk-epil 3 3270", "Silk-epil 5 5-810", "Silk-epil 9 9-720"]),
    ("Rowenta", ["EP5660", "Silence Soft"]),
    ("Polaris", ["PFE 0701A", "PFE 1402RC"]),
    ("Centek", ["CT-2191"]),
    ("Vitek", ["VT-2243", "VT-2249"]),
    ("BBK", ["BEP1000"]),
    ("Sakura", ["SA-5402", "SA-5410"]),
    ("Magnit", ["RMH-3500"]),
]
EPILATOR_COLORS = ["белый", "розовый", "сиреневый", "голубой", "чёрный"]
EPILATOR_SPECS_KEYS_VARIANTS = {
    "tweezers": ["количество пинцетов", "пинцетов", "число пинцетов"],
    "speeds": ["скоростей", "режимов скорости"],
    "attachments": ["насадок в комплекте", "сменных насадок"],
    "wet_dry": ["влажная и сухая эпиляция", "wet&dry", "влажная/сухая эпиляция"],
    "light": ["подсветка", "встроенная подсветка"],
    "battery_life": ["время работы", "автономность"],
    "power_type": ["тип питания", "питание"],
    "warranty": ["гарантия", "срок гарантии"],
}
EPILATOR_SELLERS = [
    "ИП Романова Ю.И.",
    "ООО Бьюти-Маркет",
    "Philips Official",
    "Braun Russia",
    "ООО Импорт-Тех",
    "ИП Сорокина А.Н.",
    "ООО Хоум-Стайл",
]

# ---------------------------------------------------------------------------
# Хелперы для распределений
# ---------------------------------------------------------------------------


def lognormal_price(rng: random.Random, mean: float, sigma: float, min_p: int, max_p: int) -> int:
    """Лог-нормальное распределение, обрезанное в диапазон. Округление до 10 ₽."""
    for _ in range(20):
        v = rng.lognormvariate(math.log(mean), sigma)
        v = int(round(v / 10) * 10)
        if min_p <= v <= max_p:
            return v
    return int(max(min(mean, max_p), min_p))


def beta_rating(rng: random.Random) -> float:
    """Рейтинг 1.0–5.0, перекошен к 4.5+."""
    v = rng.betavariate(9, 1.5) * 4 + 1
    return round(min(5.0, max(1.0, v)), 1)


def pareto_reviews(rng: random.Random, k: float = 1.6, floor: int = 5, cap: int = 50_000) -> int:
    """Power-law: много карточек с малыми отзывами, мало — с большими."""
    v = int(rng.paretovariate(k) * 20)
    return max(floor, min(cap, v))


def sales_proxy_from_reviews(reviews: int, rng: random.Random) -> int:
    """
    Грубая прокси-оценка продаж/мес из отзывов.
    Эмпирика категорийных менеджеров: ~5-15% покупателей пишут отзыв.
    Берём множитель ~8x и горизонт «за всё время» ≈ 12 мес → /12 → /мес.
    """
    multiplier = rng.uniform(6, 12)
    total = reviews * multiplier
    return max(1, int(total / 12))


def pick_specs(
    rng: random.Random, base_specs: dict[str, Any], variants: dict[str, list[str]]
) -> dict[str, Any]:
    """
    Конвертирует канонические specs в «как написано в карточке»:
    для каждого ключа берёт один из синонимов (по seed),
    у части карточек намеренно дропает поле — реализм.
    """
    out: dict[str, Any] = {}
    for canonical_key, value in base_specs.items():
        synonyms = variants.get(canonical_key, [canonical_key])
        display_key = rng.choice(synonyms)
        if rng.random() < 0.08:
            # 8% карточек без этого поля — реализм
            continue
        out[display_key] = value
    return out


def pick_usps(rng: random.Random, k: int = 3) -> list[str]:
    """k УТП из разных типов."""
    pools = [USP_TECH, USP_VALUE, USP_EMO, USP_SOCIAL]
    rng.shuffle(pools)
    out: list[str] = []
    for pool in pools[:k]:
        out.append(rng.choice(pool))
    return out


def make_url(category: str, sku: int) -> str:
    return f"https://www.wildberries.ru/catalog/{sku}/detail.aspx?targetUrl={category}"


# ---------------------------------------------------------------------------
# Генераторы карточек по категориям
# ---------------------------------------------------------------------------


def generate_toothbrush(rng: random.Random, idx: int) -> dict[str, Any]:
    brand, models = rng.choice(TOOTHBRUSH_BRANDS)
    model = rng.choice(models)
    ttype = rng.choice(TOOTHBRUSH_TYPE)
    color = rng.choice(TOOTHBRUSH_COLORS)
    sku = 100_000_000 + idx
    price = lognormal_price(rng, mean=1800, sigma=0.55, min_p=400, max_p=8500)
    rating = beta_rating(rng)
    reviews = pareto_reviews(rng)
    sales = sales_proxy_from_reviews(reviews, rng)
    seller = rng.choice(TOOTHBRUSH_SELLERS)
    usps = pick_usps(rng, k=3)

    base_specs = {
        "battery_life": f"{rng.choice([7, 14, 21, 30, 60])} дней",
        "movements": f"{rng.choice([24_000, 31_000, 37_000, 40_000, 48_000])} в минуту",
        "modes": rng.choice([1, 2, 3, 4, 5]),
        "heads_included": rng.choice([1, 2, 3, 4, 8]),
        "waterproof": rng.choice(["IPX7", "IPX6", "IPX5"]),
        "warranty": rng.choice(["1 год", "2 года", "3 года"]),
        "timer": rng.choice(["2 минуты", "30 секунд по квадрантам", "smart-таймер"]),
    }
    specs = pick_specs(rng, base_specs, TOOTHBRUSH_SPECS_KEYS_VARIANTS)
    name = (
        f"Зубная щётка электрическая {brand} {model} {ttype}, "
        f"цвет {color}, {usps[0]}"
    )
    description = (
        f"{brand} {model} — {ttype} электрическая зубная щётка. "
        f"{usps[1]}. {usps[2]}. "
        f"Подходит для ежедневного ухода, бережно очищает зубы и массирует дёсны. "
        f"Сертификаты: {rng.choice(CERTIFICATIONS)}."
    )
    return {
        "sku": sku,
        "category": "electric_toothbrush",
        "name": name,
        "brand": brand,
        "model": model,
        "price": price,
        "currency": "RUB",
        "rating": rating,
        "reviews_count": reviews,
        "sales_estimate_per_month": sales,
        "seller": seller,
        "url": make_url("electric_toothbrush", sku),
        "description": description,
        "specs": specs,
        "color": color,
    }


def generate_dryer(rng: random.Random, idx: int) -> dict[str, Any]:
    brand, models = rng.choice(DRYER_BRANDS)
    model = rng.choice(models)
    color = rng.choice(DRYER_COLORS)
    sku = 200_000_000 + idx
    price = lognormal_price(rng, mean=3500, sigma=0.7, min_p=700, max_p=15000)
    rating = beta_rating(rng)
    reviews = pareto_reviews(rng)
    sales = sales_proxy_from_reviews(reviews, rng)
    seller = rng.choice(DRYER_SELLERS)
    usps = pick_usps(rng, k=3)

    base_specs = {
        "power_w": f"{rng.choice([1400, 1600, 1800, 2000, 2200, 2400])} Вт",
        "modes_temp": rng.choice([2, 3, 4]),
        "modes_speed": rng.choice([1, 2, 3]),
        "attachments": rng.choice([1, 2, 3, 4]),
        "ionization": rng.choice(["есть", "нет", "есть"]),  # чаще есть
        "cool_shot": rng.choice(["есть", "нет", "есть"]),
        "motor": rng.choice(["AC", "DC", "бесщёточный"]),
        "weight_g": f"{rng.choice([350, 450, 520, 600, 680, 750])} г",
        "cord_m": f"{rng.choice([1.6, 1.8, 2.0, 2.5, 3.0])} м",
        "warranty": rng.choice(["1 год", "2 года", "3 года"]),
    }
    specs = pick_specs(rng, base_specs, DRYER_SPECS_KEYS_VARIANTS)
    name = (
        f"Фен для волос {brand} {model} профессиональный, "
        f"мощность {base_specs['power_w']}, цвет {color}, {usps[0]}"
    )
    description = (
        f"{brand} {model} — компактный профессиональный фен. "
        f"{usps[1]}. {usps[2]}. "
        f"Подходит для длинных и тонких волос, не перегревает, бережёт структуру. "
        f"Сертификаты: {rng.choice(CERTIFICATIONS)}."
    )
    return {
        "sku": sku,
        "category": "hair_dryer",
        "name": name,
        "brand": brand,
        "model": model,
        "price": price,
        "currency": "RUB",
        "rating": rating,
        "reviews_count": reviews,
        "sales_estimate_per_month": sales,
        "seller": seller,
        "url": make_url("hair_dryer", sku),
        "description": description,
        "specs": specs,
        "color": color,
    }


def generate_epilator(rng: random.Random, idx: int) -> dict[str, Any]:
    brand, models = rng.choice(EPILATOR_BRANDS)
    model = rng.choice(models)
    color = rng.choice(EPILATOR_COLORS)
    sku = 300_000_000 + idx
    price = lognormal_price(rng, mean=4200, sigma=0.6, min_p=1200, max_p=13000)
    rating = beta_rating(rng)
    reviews = pareto_reviews(rng)
    sales = sales_proxy_from_reviews(reviews, rng)
    seller = rng.choice(EPILATOR_SELLERS)
    usps = pick_usps(rng, k=3)

    base_specs = {
        "tweezers": rng.choice([20, 24, 28, 32, 40, 60]),
        "speeds": rng.choice([1, 2, 3]),
        "attachments": rng.choice([1, 2, 3, 4, 5, 7]),
        "wet_dry": rng.choice(["есть", "нет"]),
        "light": rng.choice(["есть", "нет", "есть"]),
        "battery_life": f"{rng.choice([30, 40, 50, 60])} минут",
        "power_type": rng.choice(["аккумулятор", "от сети", "аккумулятор + сеть"]),
        "warranty": rng.choice(["1 год", "2 года"]),
    }
    specs = pick_specs(rng, base_specs, EPILATOR_SPECS_KEYS_VARIANTS)
    name = (
        f"Эпилятор {brand} {model}, {base_specs['tweezers']} пинцетов, "
        f"цвет {color}, {usps[0]}"
    )
    description = (
        f"{brand} {model} — эпилятор для гладкой кожи. "
        f"{usps[1]}. {usps[2]}. "
        f"Подходит для ног, рук и зоны бикини. Эффект до 4 недель. "
        f"Сертификаты: {rng.choice(CERTIFICATIONS)}."
    )
    return {
        "sku": sku,
        "category": "epilator",
        "name": name,
        "brand": brand,
        "model": model,
        "price": price,
        "currency": "RUB",
        "rating": rating,
        "reviews_count": reviews,
        "sales_estimate_per_month": sales,
        "seller": seller,
        "url": make_url("epilator", sku),
        "description": description,
        "specs": specs,
        "color": color,
    }


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    rng = random.Random(SEED)
    products: list[dict[str, Any]] = []
    # 40 щёток, 40 фенов, 35 эпиляторов = 115 карточек
    for i in range(40):
        products.append(generate_toothbrush(rng, i))
    for i in range(40):
        products.append(generate_dryer(rng, i))
    for i in range(35):
        products.append(generate_epilator(rng, i))

    # Перемешиваем — чтобы топы по сортировке выглядели как живая выдача WB.
    rng.shuffle(products)

    OUTPUT.write_text(
        json.dumps(
            {
                "generated_with_seed": SEED,
                "total": len(products),
                "categories": sorted({p["category"] for p in products}),
                "products": products,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Самопроверка
    by_cat: dict[str, int] = {}
    for p in products:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
    print(f"Wrote {len(products)} products to {OUTPUT}")
    print(f"  by category: {by_cat}")
    pmin = min(p["price"] for p in products)
    pmax = max(p["price"] for p in products)
    print(f"  price range: {pmin}-{pmax} RUB")
    avg_rating = sum(p["rating"] for p in products) / len(products)
    print(f"  avg rating: {avg_rating:.2f}")


if __name__ == "__main__":
    main()
