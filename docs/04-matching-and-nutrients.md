# 04 · Matching and nutrient maths

## Part 1 — Matching a dish name to the database

The AI says "Aloo gobhi sabji". INDB calls it "Potato cauliflower (Aloo gobhi)". The matcher's job is to connect them correctly, and to say "not found" rather than pick the wrong dish.

### 1. Normalising spellings (`matcher.normalize`)
Indian dish names are transliterated in many ways. Both sides go through the same rules:

| Rule | Example |
|---|---|
| lower-case, punctuation → space | "Chapati/Roti" → "chapati roti" |
| `ph` → `f` | "phulka" → "fulka" |
| drop `h` (one or more) after b c d g j k p s t | "khichdi" → "kicdi", "dhokla" → "dokla", "chhole" → "cole" |
| `w`→`v`, `z`→`j`, `q`→`k` | "zeera" → "jera" |
| doubled letters → single | "meethi" → "meti", "chhole" → "cole" |
| plural `s` removed (words longer than 3) | "pakodas" → "pakoda" |
| final `y` → `i` | "idly" → "idli" |
| filler words removed | with, and, of, ka, ki, ke, aur, style, homemade, recipe… |

So "Khichri", "khichdi", "Kichdi" and "Khitchdi" all become nearly the same text.

### 2. Name variants (`matcher.name_variants`)
INDB names pack several names into one. Each becomes a separate searchable variant:
- "Plain khitchdi (Plain khichri/khichdi)" → *plain khitchdi*, *plain khichri*, *plain khichdi*, full name
- "Onion-green chilli parantha/paratha" → *onion green chilli parantha*, *onion green chilli paratha*

A single word after `/` replaces only the last word, so plain "paratha" never becomes a variant of the onion paratha. In total, 1,014 foods give **2,161 variants**.

### 3. Speed: trigram shortlist
Each variant is split into 3-letter pieces ("dhokla" → " d", " do", "dok", "okl"…). For a query, the 60 variants sharing the most pieces get the full comparison. Result: **~6 ms per lookup**, down from 165 ms.

### 4. Similarity score (0–1)
`score = ½ × letter similarity + ½ × word overlap`
- **Letter similarity:** Python's `SequenceMatcher` ratio on the whole normalised text.
- **Word overlap:** F1 of matching words, where two words "match" if at least 80 % similar (so "gobhi" ≈ "gobi").

An exact normalised match scores **1.0**.

### 5. Order of checks (`matcher.match`)

| Order | Check | Used when | Score |
|---|---|---|---|
| 1 | **Personal alias** (this user corrected this name before) | Photo-only scans | 1.0 |
| 2 | **Global alias** (`aliases.json` + nutritionist-approved) | Always | 1.0 |
| 3 | **Fuzzy** best of all names | Always | 0–1 |

The matcher tries the AI's main name first, then its `search_terms`. It records `viaMainName`: whether the hit came from the main name.

### 6. Decision (`engine.analyze`, step 3)

```
                      ┌─ personal alias ───────────────────────────► ACCEPT
match result ─────────┼─ main name: alias, or score ≥ 0.86 ─────────► ACCEPT
                      ├─ score 0.50–0.86, or hit only via other name ► ASK AI to choose ─► ACCEPT or NONE
                      └─ score < 0.50 ───────────────────────────────► NONE (AI estimate)
```
Thresholds: `FOOD_MATCH_AUTO_THRESHOLD` and `FOOD_MATCH_ASK_THRESHOLD`.

**Why "other names" are double-checked:** in testing, the AI listed "chicken pulao" as another name for chicken biryani, and the engine matched pulao. Biryani and pulao are different dishes. The rule (plus a prompt fix) means such matches are now double-checked, and the AI correctly said "none".

### Measured results (common dishes)

| AI said | Matched | How |
|---|---|---|
| Khaman dhokla | Khaman (dhokla) | fuzzy 1.00 |
| Idly | Idli | fuzzy 1.00 |
| Phulka | Chapati/Roti | alias |
| Palak paneer | Spinach paneer (Palak paneer) | fuzzy 1.00 |
| Aloo gobi sabzi | Potato cauliflower (Aloo gobhi) | AI choice |
| Raita | Cucumber raita | AI choice |
| Steamed white rice | Boiled rice (Uble chawal) | AI choice |
| Chicken biryani | — (not in INDB) | AI estimate ✓ correct |
| Dal tadka, Toor dal | — (no plain toor dal in INDB) | AI estimate |
| Banana, Curd, Buttermilk | — (INDB has recipes, not raw foods) | AI estimate |

### Improving matching
- **Add aliases** to `food/data/aliases.json`. This is the cheapest, most effective improvement. Check the code with search first.
- Nutritionist-approved aliases are added automatically ([05](05-learning-and-fake-data.md)).
- **Add raw foods** (fruit, curd, milk, eggs) from IFCT. See [07](07-data-sources-and-quality.md).

---

## Part 2 — Nutrient maths

### Per 100 g × grams
INDB stores every nutrient **per 100 g of the cooked dish**.
```
nutrient for this item = value per 100 g × grams ÷ 100
```
Example, 2 phulka (75 g), INDB Chapati/Roti = 202.4 kcal/100 g → 202.4 × 75 ÷ 100 = **151.8 kcal**.

### AI estimates rescale the same way
The AI gives an estimate for *its* grams. The engine converts it to per 100 g (`to_per100g`) and keeps it. If the user later changes the amount, the estimate scales exactly like database values.

Example: AI said 120 g dhokla = 192 kcal → 160 kcal/100 g → user changes to 150 g → 240 kcal.

### Totals
Meal total = sum of each nutrient over all items. Day total (diary) = sum over the day's meals.

### The 38 nutrients
Key names include the unit:

| Group | Keys |
|---|---|
| Energy & macros | `energy_kcal`, `protein_g`, `carb_g`, `fat_g`, `fibre_g`, `freesugar_g` |
| Fats | `sfa_mg` (saturated), `mufa_mg`, `pufa_mg`, `cholesterol_mg` |
| Minerals | `sodium_mg`, `potassium_mg`, `calcium_mg`, `phosphorus_mg`, `magnesium_mg`, `iron_mg`, `zinc_mg`, `copper_mg`, `manganese_mg`, `selenium_ug`, `chromium_mg`, `molybdenum_mg` |
| Vitamins | `vita_ug`, `carotenoids_ug`, `vitb1_mg`, `vitb2_mg`, `vitb3_mg`, `vitb5_mg`, `vitb6_mg`, `vitb7_ug`, `vitb9_ug`, `folate_ug`, `vitc_mg`, `vitd2_ug`, `vitd3_ug`, `vite_mg`, `vitk1_ug`, `vitk2_ug` |

- **Database items** have all 38 (where INDB has data).
- **AI-estimate items** have 14: energy, protein, carbs, fat, fibre, free sugar, saturated fat, cholesterol, sodium, potassium, calcium, iron, vitamin A and vitamin C.
- Note that saturated/mono/poly fats are in **mg**, as in INDB. Divide by 1,000 to show grams.

### Units the user can pick (`unitOptions`)
Built per item, duplicates removed:
1. the AI's unit, with grams per unit = AI grams ÷ quantity (e.g. piece = 30 g)
2. the user's own saved sizes (`yours: true`)
3. the INDB household serving (e.g. "dhokla" = 118.7 g); this can be odd, so it's never the default
4. `g` (1 g)

When the user switches unit in the app, the weight stays the same and only the count changes.
