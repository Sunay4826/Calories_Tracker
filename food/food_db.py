"""Loads the food database (food/data/foods.json) once and keeps it in memory.

foods.json is built from INDB by scripts/build_food_db.py. Each food:
    {"code": "ASC123", "name": "Khaman (dhokla)", "source": "INDB 2024.11",
     "per100g": {"energy_kcal": ..., "protein_g": ..., ...},
     "serving": {"unit": "piece", "grams": 30.0} | null,
     "quality": "ok" | "suspect", "qualityNote": "..."}
"""
import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


class FoodDb:
    def __init__(self, foods: list[dict]):
        self.foods = foods
        self.by_code = {food["code"]: food for food in foods}

    def get(self, code: str | None) -> dict | None:
        return self.by_code.get(code) if code else None

    @classmethod
    def load(cls, path: Path = DATA_DIR / "foods.json") -> "FoodDb":
        return cls(json.loads(path.read_text(encoding="utf-8")))


@lru_cache
def get_food_db() -> FoodDb:
    return FoodDb.load()


def load_seed_aliases(path: Path = DATA_DIR / "aliases.json") -> dict[str, str]:
    """Hand-made common names -> food code (e.g. "phulka" -> Chapati/Roti)."""
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {name: code for name, code in raw.items() if not name.startswith("_")}
