"""Checks and cleans the AI's JSON before the engine trusts any of it.

Same spirit as fud-ai's FoodJsonParser: the AI can return missing fields,
strings instead of numbers or impossible values. Every rule here is a guard;
when a value fails, it is fixed or dropped and a warning is recorded.
"""
from dataclasses import dataclass, field

from .nutrients import AI_ESTIMATE_KEYS, macro_energy

MAX_KCAL_PER_GRAM = 9.0  # pure fat; nothing edible is denser
MIN_GRAMS, MAX_GRAMS = 1.0, 3000.0


@dataclass
class AiItem:
    name: str
    search_terms: list[str]
    quantity: float
    unit: str
    grams: float | None
    cooking_style: str
    confidence: float
    alternatives: list[str]
    estimate: dict[str, float] | None  # nutrients for `grams`, or None if unusable


@dataclass
class Recognition:
    not_food: bool
    transcript: str
    items: list[AiItem]
    warnings: list[str] = field(default_factory=list)


def _number(value, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and number not in (float("inf"), float("-inf")) else default


def _strings(value, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [" ".join(str(v).split())[:80] for v in value if str(v).strip()]
    return list(dict.fromkeys(cleaned))[:limit]


def clean_estimate(raw, grams: float | None, warnings: list[str], name: str) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    estimate = {}
    for key in AI_ESTIMATE_KEYS:
        value = _number(raw.get(key))
        if value is not None and value >= 0:
            estimate[key] = round(value, 2)
    if not estimate:
        return None
    if "energy_kcal" not in estimate and any(k in estimate for k in ("carb_g", "protein_g", "fat_g")):
        estimate["energy_kcal"] = round(macro_energy(estimate), 1)
    if grams and estimate.get("energy_kcal", 0) > grams * MAX_KCAL_PER_GRAM:
        warnings.append(f"Dropped AI nutrient estimate for '{name}': more than 9 kcal per gram.")
        return None
    return estimate


def clean_item(raw: dict, warnings: list[str]) -> AiItem | None:
    name = " ".join(str(raw.get("name") or "").split())[:80]
    if not name:
        warnings.append("Dropped an item with no name.")
        return None

    grams = _number(raw.get("grams"))
    if grams is not None and not (MIN_GRAMS <= grams <= MAX_GRAMS):
        warnings.append(f"Ignored impossible weight {grams:g} g for '{name}'.")
        grams = None
    quantity = _number(raw.get("quantity"), 1.0) or 1.0
    if quantity <= 0 or quantity > 100:
        quantity = 1.0
    unit = " ".join(str(raw.get("unit") or "serving").split()).lower()[:30] or "serving"
    confidence = min(max(_number(raw.get("confidence"), 0.5) or 0.0, 0.0), 1.0)

    return AiItem(
        name=name,
        search_terms=_strings(raw.get("search_terms"), 5),
        quantity=round(quantity, 2),
        unit=unit,
        grams=round(grams, 1) if grams else None,
        cooking_style=str(raw.get("cooking_style") or "")[:40],
        confidence=round(confidence, 2),
        alternatives=_strings(raw.get("alternatives"), 3),
        estimate=clean_estimate(raw.get("estimate"), grams, warnings, name),
    )


def parse_recognition(raw: dict, max_items: int) -> Recognition:
    warnings: list[str] = []
    raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []
    if len(raw_items) > max_items:
        warnings.append(f"Only the first {max_items} items were kept.")
        raw_items = raw_items[:max_items]
    items = [item for item in (clean_item(r, warnings) for r in raw_items if isinstance(r, dict)) if item]
    return Recognition(
        not_food=bool(raw.get("not_food")) and not items,
        transcript=" ".join(str(raw.get("transcript") or "").split())[:1000],
        items=items,
        warnings=warnings,
    )
