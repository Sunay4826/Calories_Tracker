# 01 · Overview

## The goal

The user opens the app, takes a photo of their thali (or types "2 phulka, dal and aloo gobi", or says it), and gets every nutrient: calories, protein, carbs, fat, fibre, sugar, sodium, iron, vitamins and more. The numbers should be **accurate for Indian home food**, the user should be able to **correct mistakes**, and the system should **get better over time** without being fooled by fake input.

## Why two "brains": AI + database

| | AI (Gemini) | Database (INDB) |
|---|---|---|
| Good at | Seeing a photo, understanding "1 katori dal", splitting a thali into dishes, guessing grams | Exact, lab-derived nutrient values per 100 g |
| Bad at | Exact nutrient numbers (it estimates; it can vary) | Can't see photos or read sentences |

So each does what it's good at: **the AI recognises, the database measures.** If the database has no entry, or its entry looks wrong, the AI's own estimate is used and clearly marked "≈ AI estimate".

## The four building blocks

```
┌────────────┐   ┌─────────────┐   ┌────────────────┐   ┌──────────────────┐
│ 1. AI      │──►│ 2. MATCHER  │──►│ 3. NUTRIENTS   │──►│ 4. LEARNING      │
│ ai_client  │   │ matcher.py  │   │ nutrients.py   │   │ learning.py      │
│ prompts    │   │ aliases.json│   │ foods.json     │   │ trust + review   │
│ parsing    │   │             │   │                │   │ queue            │
└────────────┘   └─────────────┘   └────────────────┘   └──────────────────┘
   "what is it,     "which INDB       "how much of each     "remember fixes,
    how much?"       entry is it?"     nutrient?"            safely"
```

`engine.py` runs them in order; `api.py` exposes them as HTTP routes; `store.py` saves everything.

## Key decisions (and why)

| Decision | Why |
|---|---|
| **Gemini key stays on the server** | Anything inside the app can be extracted (a mobile app can ship its own `.env` as an asset). The app only calls this API with a signed login token. |
| **One small FastAPI service** | A single process with signed-token auth, MongoDB and rate limits; runs locally with JSON files and deploys as one Docker container. |
| **INDB as the database** | Largest free source of *cooked Indian recipes* (1,014), 38 nutrients, from Anuvaad (2024). IFCT has raw ingredients, not dishes. |
| **AI grams first, database serving second** | INDB's household servings are often odd (a "small bowl" of khichdi = 574 g). The AI sees the actual plate. |
| **Database rows can be "suspect"** | 118 INDB dishes (mostly deep-fried) count all of the frying oil, e.g. poori at 78 g fat per 100 g. Those use the AI estimate instead. |
| **Cross-check DB vs AI** | If they differ by more than 2.5× on calories, the item is flagged for the user and queued for a nutritionist to check the database row. |
| **Only the main dish name can auto-match** | When the AI's "other names" were used to match, the AI double-checks. This stopped "chicken biryani" from becoming "chicken pulao". |
| **Typed or spoken names are believed** | Personal name fixes only apply to photo-only scans. If a user types "paratha", they get paratha. |
| **Photos are never stored** | Only a SHA-256 hash is kept (privacy, DPDP Act). Photo training would need a separate consented upload. |
| **Global learning needs people, not one person** | 3+ trusted users must agree, and then a nutritionist approves. One user can't change data for everyone. |
| **Can switch to Claude on Bedrock** | If you already pay for Bedrock, set `FOOD_AI_PROVIDER=bedrock` (photo + text; no audio). |

## What the user sees

1. **Capture:** camera / gallery / type / 🎤 mic.
2. **"Is this correct?"** One card per dish:
   - name, amount (e.g. "4 piece · 120 g"), kcal/protein/carbs/fat
   - **✓ Verified · INDB 2024.11** or **≈ AI estimate**
   - ⚠ orange messages when something needs a look
   - buttons: **Change amount** · **Not this?** · **Use AI estimate** (when the numbers disagree) · **Remove**
   - **＋ Add a missed item**
3. **Save** → the food diary with daily totals.

## Where things are

| Path | What |
|---|---|
| `food/` | The engine (FastAPI routes + logic) |
| `web/index.html` | Web client: log a meal, diary, nutritionist queue |
| `main.py` | API server (local and Docker) |
| `scripts/` | Build the food database; export training data |
| `tests/` | 42 automated tests |
| `data/raw/` | The original INDB Excel file (not committed) |
| `docs/` | This documentation |
