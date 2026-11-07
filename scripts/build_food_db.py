"""Build food/data/foods.json from the INDB Excel file.

Usage (from the food-service folder):
    .venv\\Scripts\\python scripts\\build_food_db.py
    .venv\\Scripts\\python scripts\\build_food_db.py path\\to\\Anuvaad_INDB_2024.11.xlsx

What it does:
1. Reads every row of the INDB sheet (1,014 Indian recipes).
2. Keeps the per-100 g nutrient columns and the household serving
   (e.g. "bowl", "piece") with its weight in grams.
3. Flags rows whose numbers look wrong (see is_suspect) so the engine
   never shows them to a user as "verified".
4. Writes food/data/foods.json, which the engine loads at startup.
"""
import json
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_XLSX = ROOT / "data" / "raw" / "Anuvaad_INDB_2024.11.xlsx"
OUT = ROOT / "food" / "data" / "foods.json"

# Per-100 g nutrient columns copied from INDB, unit is in the name.
NUTRIENT_COLUMNS = [
    "energy_kcal", "energy_kj", "carb_g", "protein_g", "fat_g", "freesugar_g", "fibre_g",
    "sfa_mg", "mufa_mg", "pufa_mg", "cholesterol_mg",
    "calcium_mg", "phosphorus_mg", "magnesium_mg", "sodium_mg", "potassium_mg", "iron_mg",
    "copper_mg", "selenium_ug", "chromium_mg", "manganese_mg", "molybdenum_mg", "zinc_mg",
    "vita_ug", "vite_mg", "vitd2_ug", "vitd3_ug", "vitk1_ug", "vitk2_ug", "folate_ug",
    "vitb1_mg", "vitb2_mg", "vitb3_mg", "vitb5_mg", "vitb6_mg", "vitb7_ug", "vitb9_ug",
    "vitc_mg", "carotenoids_ug",
]

# Foods that really are mostly fat, so a high fat share is not an error.
NATURALLY_FATTY = ("mayonnaise", "dressing", "tartare", "baghar", "tadka", "icing", "pickle", "aachar")


def is_suspect(name: str, per100: dict) -> str | None:
    """Return a reason if the row's numbers are not believable, else None.

    INDB's deep-fried dishes (poori, pakora, samosa, kofta, cutlet, vada ...)
    count all of the frying oil as if the food absorbed it, which gives
    60-90 g fat per 100 g. Real values are far lower. We flag any dish where
    fat is >= 40 g/100 g AND fat supplies >= 80 % of the energy.
    """
    kcal = per100.get("energy_kcal") or 0
    fat = per100.get("fat_g") or 0
    if kcal > 900:
        return "energy above 900 kcal/100 g is physically impossible"
    lowered = name.lower()
    if any(word in lowered for word in NATURALLY_FATTY):
        return None
    if fat >= 40 and kcal and (fat * 9) / kcal >= 0.80:
        return "frying oil appears fully counted (fat >= 40 g/100 g, >= 80 % of energy)"
    return None


def main(xlsx_path: Path) -> None:
    sheet = openpyxl.load_workbook(xlsx_path, read_only=True).worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows)

    foods, suspect = [], 0
    for values in rows:
        row = dict(zip(header, values))
        if not row.get("food_code") or not row.get("food_name"):
            continue
        name = " ".join(str(row["food_name"]).split())
        per100 = {
            col: round(float(row[col]), 3) for col in NUTRIENT_COLUMNS if row.get(col) not in (None, "")
        }

        # INDB gives nutrients for one serving; serving grams = serving kcal / kcal per gram.
        serving = None
        unit = (row.get("servings_unit") or "").strip()
        unit_kcal = row.get("unit_serving_energy_kcal")
        if unit and unit_kcal and per100.get("energy_kcal"):
            grams = unit_kcal / per100["energy_kcal"] * 100
            if 1 <= grams <= 2000:
                serving = {"unit": unit, "grams": round(grams, 1)}

        reason = is_suspect(name, per100)
        suspect += bool(reason)
        foods.append({
            "code": row["food_code"],
            "name": name,
            "source": "INDB 2024.11",
            "primarySource": row.get("primarysource") or "",
            "per100g": per100,
            "serving": serving,
            "quality": "suspect" if reason else "ok",
            "qualityNote": reason or "",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(foods, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {len(foods)} foods to {OUT} ({suspect} flagged as suspect)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX)
