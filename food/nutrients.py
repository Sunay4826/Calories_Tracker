"""Nutrient names, units and the "per 100 g x grams" maths.

Keys follow INDB column names so database values and AI estimates line up:
the unit is part of the key (energy_kcal, protein_g, sodium_mg, vita_ug ...).
"""

# key -> (label shown to users, unit). Order = display order.
NUTRIENTS: dict[str, tuple[str, str]] = {
    "energy_kcal": ("Energy", "kcal"),
    "protein_g": ("Protein", "g"),
    "carb_g": ("Carbohydrates", "g"),
    "fat_g": ("Fat", "g"),
    "fibre_g": ("Fibre", "g"),
    "freesugar_g": ("Free sugar", "g"),
    "sfa_mg": ("Saturated fat", "mg"),
    "mufa_mg": ("Monounsaturated fat", "mg"),
    "pufa_mg": ("Polyunsaturated fat", "mg"),
    "cholesterol_mg": ("Cholesterol", "mg"),
    "sodium_mg": ("Sodium", "mg"),
    "potassium_mg": ("Potassium", "mg"),
    "calcium_mg": ("Calcium", "mg"),
    "phosphorus_mg": ("Phosphorus", "mg"),
    "magnesium_mg": ("Magnesium", "mg"),
    "iron_mg": ("Iron", "mg"),
    "zinc_mg": ("Zinc", "mg"),
    "copper_mg": ("Copper", "mg"),
    "manganese_mg": ("Manganese", "mg"),
    "selenium_ug": ("Selenium", "µg"),
    "chromium_mg": ("Chromium", "mg"),
    "molybdenum_mg": ("Molybdenum", "mg"),
    "vita_ug": ("Vitamin A", "µg"),
    "carotenoids_ug": ("Carotenoids", "µg"),
    "vitb1_mg": ("Vitamin B1 (thiamine)", "mg"),
    "vitb2_mg": ("Vitamin B2 (riboflavin)", "mg"),
    "vitb3_mg": ("Vitamin B3 (niacin)", "mg"),
    "vitb5_mg": ("Vitamin B5", "mg"),
    "vitb6_mg": ("Vitamin B6", "mg"),
    "vitb7_ug": ("Vitamin B7 (biotin)", "µg"),
    "vitb9_ug": ("Vitamin B9", "µg"),
    "folate_ug": ("Folate", "µg"),
    "vitc_mg": ("Vitamin C", "mg"),
    "vitd2_ug": ("Vitamin D2", "µg"),
    "vitd3_ug": ("Vitamin D3", "µg"),
    "vite_mg": ("Vitamin E", "mg"),
    "vitk1_ug": ("Vitamin K1", "µg"),
    "vitk2_ug": ("Vitamin K2", "µg"),
}

# The subset the AI is asked to estimate when a dish is not in the database.
# What a saved meal keeps when FOOD_STORE_DETAILS=false (agreed 30 Oct 2026).
DIARY_NUTRIENTS = ["energy_kcal", "protein_g", "fat_g", "fibre_g", "freesugar_g", "sodium_mg"]

AI_ESTIMATE_KEYS = [
    "energy_kcal", "protein_g", "carb_g", "fat_g", "fibre_g", "freesugar_g",
    "sfa_mg", "cholesterol_mg", "sodium_mg", "potassium_mg", "calcium_mg", "iron_mg",
    "vita_ug", "vitc_mg",
]


def scale(per100g: dict[str, float], grams: float) -> dict[str, float]:
    """Nutrients for `grams` of a food, given its values per 100 g."""
    factor = grams / 100.0
    return {key: round(per100g[key] * factor, 2) for key in NUTRIENTS if per100g.get(key) is not None}


def to_per100g(amounts: dict[str, float], grams: float) -> dict[str, float]:
    """Inverse of scale(): turn an AI estimate for `grams` into per-100 g values."""
    if grams <= 0:
        return {}
    return {key: value * 100.0 / grams for key, value in amounts.items()}


def add_totals(items: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in items:
        for key, value in (item.get("nutrients") or {}).items():
            totals[key] = round(totals.get(key, 0.0) + value, 2)
    return {key: totals[key] for key in NUTRIENTS if key in totals}


def macro_energy(n: dict[str, float]) -> float:
    """Energy implied by the macros (4 kcal/g carbs & protein, 9 kcal/g fat)."""
    return 4 * (n.get("carb_g") or 0) + 4 * (n.get("protein_g") or 0) + 9 * (n.get("fat_g") or 0)
