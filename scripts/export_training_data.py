"""Export confirmed meals as training data for your own model (Colab, later).

Usage (local test data):
    .venv\\Scripts\\python scripts\\export_training_data.py            -> training_data.jsonl
Inside host-backend (MongoDB), run the same file with FOOD_STORE=mongo from
the backend container so app.core.database is importable.

One line per confirmed item:
    {"input": "text"|"photo"|"voice", "userText": "...", "imageSha256": "...",
     "aiName": "Paratha", "label": "OSR104", "labelName": "Methi thepla",
     "grams": 100, "userEdited": true, "trusted": true}

"trusted" = the label came from the database (not an AI estimate) and the user
who confirmed it has trust >= 0.5. Train on trusted rows; use the rest only
after a nutritionist checks them. Photos are NOT stored by the engine (only a
hash), so photo training needs a separate, consented image upload (see
docs/05-learning-and-fake-data.md).
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FOOD_STORE", "json")
os.environ.setdefault("FOOD_JSON_STORE_DIR", str(ROOT / "local_store"))

from food.store import ANALYSES, MEALS, PROFILES, get_store  # noqa: E402


async def main(out_path: Path) -> None:
    store = get_store()
    meals = await store.find(MEALS, limit=1_000_000)
    trust = {p["id"]: p.get("trust", 0.5) for p in await store.find(PROFILES, limit=1_000_000)}
    rows = 0
    with out_path.open("w", encoding="utf-8") as out:
        for meal in meals:
            analysis = await store.get(ANALYSES, meal["analysisId"]) or {}
            for item in meal["items"]:
                match = item.get("match") or {}
                out.write(json.dumps({
                    "input": "+".join(meal.get("inputKind", [])),
                    "userText": analysis.get("userText"),
                    "transcript": analysis.get("transcript"),
                    "imageSha256": analysis.get("imageSha256"),
                    "aiName": item.get("originalAiName") or item.get("aiName"),
                    "label": match.get("foodCode"),
                    "labelName": match.get("foodName") or item.get("name"),
                    "grams": item.get("grams"),
                    "userEdited": item.get("edited", False),
                    "trusted": item.get("source") == "database" and trust.get(meal["userId"], 0.5) >= 0.5,
                }, ensure_ascii=False) + "\n")
                rows += 1
    print(f"Wrote {rows} rows from {len(meals)} meals to {out_path}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "training_data.jsonl"))
