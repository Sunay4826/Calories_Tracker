# 07 · Data sources and quality

## What's in the database now

| | |
|---|---|
| Source | **INDB (Indian Nutrient Databank) 2024.11** by Anuvaad Solutions |
| File | `data/raw/Anuvaad_INDB_2024.11.xlsx`, downloaded 29 Oct 2026 from `anuvaad.org.in/wp-content/uploads/2020/07/Anuvaad_INDB_2024.11.xlsx` |
| Dishes | **1,014 cooked recipes**:<br>• `ASC…` = 490 (Anuvaad "asc_manual")<br>• `BFP…` = 376 ("bfp_manual")<br>• `OSR…` = 148 (open-source recipes) |
| Nutrients | 38 per 100 g (+ energy in kJ) |
| Household servings | 917 dishes have one (bowl, piece, roti, glass…) |
| Built into | `food/data/foods.json` by `scripts/build_food_db.py` |

### Coverage
- **Good:** dals, sabzis, curries, rotis/parathas, rice dishes, South Indian (idli, dosa, sambar, upma), snacks, sweets, chutneys, raitas, drinks.
- **Missing:**
  - raw foods (fruit, curd, milk, eggs as ingredients);
  - some everyday dishes: plain toor/arhar dal tadka, chicken biryani, chaas.
  - These fall back to the AI estimate.

## ⚠️ Quality problems found (important)

### 1. Deep-fried dishes count all the frying oil (118 flagged)
Many fried dishes show **60–90 g fat per 100 g**, with fat supplying 85–95 % of the energy. Real values are far lower; the recipe calculation seems to have counted all the oil in the pan as eaten.

| Dish | INDB | Realistic |
|---|---|---|
| Poori | 738 kcal, 78 g fat /100 g | ~300–350 kcal |
| Bhatura | 793 kcal, 83 g fat | ~300–350 kcal |
| Aloo pakora | 677 kcal, 72 g fat | ~250–320 kcal |
| Dum aloo | 682 kcal, 74 g fat | ~120–180 kcal |
| Rice moong dal cheela | 798 kcal, 82 g fat | ~150–200 kcal |

**What the engine does:**
- `build_food_db.py` marks a row **suspect** when fat ≥ 40 g/100 g **and** fat ≥ 80 % of energy (or energy > 900 kcal/100 g).
- Truly fatty items (mayonnaise, dressings, tartare, tadka/baghar, icing, pickles) are excluded from the flag.
- Suspect rows are still **matched** (so the name is right), but the **AI estimate** is used for nutrients, with the message "Database value looked wrong for this dish".

**Result:** 118 of 1,014 rows are flagged.

### 2. Some rows disagree with each other
- "Khaman (dhokla)" **55 kcal/100 g** vs "Dhokla" **216**, so khaman is almost certainly wrong.
- "Hot tea (Garam Chai)" 16 kcal/100 g. That may be correct for tea with little milk and no sugar, but most Indian chai is ~50–60.
- "Poha" 294 kcal/100 g looks high for cooked poha (~130–180).

**What the engine does:** if database and AI calories differ by more than 2.5×, the item is flagged and a **data check** goes to the nutritionist queue. A nutritionist can mark the row wrong (→ AI estimate for everyone).

### 3. Household serving sizes are unreliable
- A "small bowl" of khichdi is listed as 574 g, a "bowl" of kofta curry as 780 g.
- So the AI's gram estimate is the default, and INDB servings are only offered as an extra unit option.

### Recommended data clean-up (by a nutritionist, one-off)
1. Go through the 118 suspect rows (`"quality": "suspect"` in `foods.json`). Recalculate them with realistic oil absorption (typically 10–20 % of fried weight), or accept the AI estimate.
2. Review every `data_conflict` item in the queue as it appears.
3. Put corrected values in an extra file (e.g. `food/data/corrections.json`) and apply it in `build_food_db.py`, so INDB updates don't wipe the fixes. *(This step is not built yet.)*

## ⚖️ Licence: action needed before production
- INDB is described as **"open access"** but **no licence is stated** on the file or page.
- Before launching, email Anuvaad for **written permission for commercial use in an app**:
  - **Contact:** awasthi@anuvaad.org.in, +91-98102 14871
  - Mention: your app name, number of users, that the data is used server-side to show nutrients, and that INDB is credited.
- **Credit in the app:** the item badge already shows "Verified · INDB 2024.11". Add a line in the app's About/Credits: "Nutrient data: Indian Nutrient Databank (INDB), Anuvaad Solutions."
- The prompts are adapted from **fud-ai** (MIT License, © Apoorv Darshan). The MIT licence only requires keeping the copyright notice, which is in `prompts.py`.

## Adding more data later

| Source | Adds | Licence / notes |
|---|---|---|
| **IFCT 2017** (ICMR-NIN) | ~528 raw foods (fruit, vegetables, grains, milk, eggs, meat), 151 nutrients | Free PDF at `nin.res.in`. The GitHub CSV (`nodef/ifct2017`) is **AGPL-3.0**, so avoid it in a closed product. Ask NIN (nin@ap.nic.in) for permission/data |
| **USDA FoodData Central** | Global raw foods (banana, apple, milk) | Public domain (CC0); free downloads |
| **Your nutritionists** | Corrected and missing app-specific dishes | Yours |

How to add a source:
1. Write a small converter that outputs the same shape as `foods.json`. Use a new code prefix (`IFCT…`, `USDA…`) and the same nutrient keys.
2. Merge it in `build_food_db.py`.
3. Add aliases for common names ("banana" → the USDA code).
4. Re-run the build and the tests.

## Rebuilding the database
```powershell
.venv\Scripts\python scripts\build_food_db.py                       # uses data/raw/Anuvaad_INDB_2024.11.xlsx
.venv\Scripts\python scripts\build_food_db.py path\to\newer_INDB.xlsx
.venv\Scripts\python -m pytest -q                                    # make sure nothing broke
```
If INDB changes codes, check `food/data/aliases.json`: aliases pointing to missing codes are silently ignored.
