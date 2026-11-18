# 03 · Request flow, step by step

This follows one real meal through the system: **"4 pieces khaman dhokla with green chutney"**, tested live on 29 Oct 2026.

## A. Analyze: `POST /api/auth/food/analyze`

### Step 0 · The app sends the meal
```json
{
  "phoneNumber": "9876543210",
  "text": "4 pieces khaman dhokla with green chutney",
  "imageBase64": null,
  "mealType": "snack"
}
```
Header: `Authorization: Bearer <login token>`.

- **Photo:** the app sends a base64 JPEG instead of (or as well as) `text`. With both, the text is context for the photo.
- **Voice:** the phone turns speech into text first, so it arrives as `text` (see [09](09-voice-and-text.md)).

### Step 1 · Who is it? (`api.py` → `auth.py`)
- The token must belong to this phone (the signed token's `sub` claim). Otherwise **401/403**.
- Rate limit: at most 10 scans per minute and 150 per day for this user. Otherwise **429**.

### Step 2 · Check the input (`engine.analyze`)
- At least one of text, photo or voice is needed. Otherwise **400**.
- **Photo:**
  - allowed types are JPEG/PNG/WebP/HEIC; the photo must be valid base64 and at most 6 MB (otherwise **400/413**);
  - a SHA-256 hash is calculated; **the photo itself is never saved**.
- **Text:** extra spaces are removed and it is cut to 1,000 characters.

### Step 3 · Ask the AI (`prompts.recognition_prompt` → `ai_client` → `parsing`)
The AI gets the instructions plus the input and returns:
```json
{"not_food": false, "transcript": "", "items": [
  {"name": "Khaman dhokla", "search_terms": ["dhokla", "steamed gram flour cake"],
   "quantity": 4, "unit": "piece", "grams": 120, "confidence": 0.95,
   "alternatives": ["Nylon dhokla", "Idli"],
   "estimate": {"energy_kcal": 192, "protein_g": 8.4, "carb_g": 24, "fat_g": 7.2, ...}},
  {"name": "Green chutney", "quantity": 2, "unit": "tbsp", "grams": 30, ...}
]}
```
**For photos** the prompt adds counting rules:
- scan the plate in order, one entry per pile, bowl or piece, including small sides;
- count only the pieces that are visible;
- dishes served together are separate entries (bhatura + chole, vada + pav);
- size portions against the plate;
- skip water and garnish.

Together with temperature 0 and a fixed seed, the same photo now gives the same answer every time. Measured on 30 Oct 2026, 5 runs each: a 12-item banana-leaf thali went from 885–1,575 kcal to 1,443 kcal every run. Chole bhature, vada pav and rajma chawal were also identical across runs.

If a long reply comes back with a missing `}` (seen on busy plates), `ai_client.extract_json` repairs it. If the reply still can't be read, the engine asks again.

`parsing.py` then checks every field (impossible grams, more than 9 kcal per gram, missing names…).

- If the AI is busy, the call retries after 1 s, 2 s and 4 s, then returns **502**.
- If there is no food → `{"notFood": true, "message": "No food found..."}` and nothing is saved.

### Step 4 · Find each dish in the database (`matcher.match`)
> Steps 4–5 run only when `FOOD_USE_DATABASE=true`. With the default `false`, see [AI-only mode](#ai-only-mode-food_use_databasefalse) at the end of this page.

For each dish, the matcher tries its name and its `search_terms`, in this order:

1. **Personal alias** (photo-only scans): has *this user* corrected this name before?
2. **Global alias**: seed list (`aliases.json`) or nutritionist-approved. "Green chutney" → `OSR098` ✓
3. **Fuzzy**: compare against all 2,161 name variants. "Khaman dhokla" vs "Khaman (dhokla)" scores 1.00 → `BFP439` ✓

### Step 5 · Unsure? Ask the AI to choose (`engine._ai_choose`)
A match is **sure** when:
- it is the user's own alias, or
- the **main dish name** hit an alias, or
- the main dish name scored ≥ 0.86.

Everything scoring 0.50–0.86, or matched only through an "other name", is collected into **one** cheap text-only AI call:
> "Aloo gobi sabzi" — candidates: Potato curry, Potato cauliflower (Aloo gobhi), … → pick the SAME dish or null.

Whatever is still unsure after that is treated as "not in database".

### Step 6 · Build each item (`engine._build_item`)

**Grams**, highest priority first:

| Priority | Source | `gramsSource` |
|---|---|---|
| 1 | user typed grams (fix) | `user` |
| 2 | this user's usual size for this dish+unit | `your_usual` |
| 3 | AI's estimate | `ai` |
| 4 | database serving × quantity | `database_serving` (flagged) |
| 5 | 100 g | `default` (flagged) |

**Nutrients:**

| Situation | Numbers used | Badge | Flag |
|---|---|---|---|
| Matched, database row OK | INDB per 100 g × grams | ✓ Verified | — |
| Matched, but DB and AI differ more than 2.5× on kcal | INDB (AI shown alongside) | ✓ Verified | `ai_db_disagree` + nutritionist data check |
| Matched, but row is "suspect" (e.g. poori) | AI estimate | ≈ AI estimate | `database_value_unreliable` |
| Not in database | AI estimate | ≈ AI estimate | `not_in_database` |
| AI confidence < 0.6 | (either) | | `low_confidence` |

For dhokla: INDB says 54.7 kcal/100 g → 120 g = **65.7 kcal**. The AI says **192 kcal**. That's 2.9× apart → flagged, and a "data check" for `BFP439` goes into the nutritionist queue. (INDB's plain "Dhokla", `ASC474`, has 216 kcal/100 g, so the khaman row is very likely wrong.)

### Step 7 · Save and reply
The analysis is saved in `food_analyses` with status `pending`. Reply (trimmed, real):
```json
{
  "analysisId": "0cac9071396e411085fceb0886ed1d47",
  "status": "pending",
  "items": [
    {
      "itemId": "a5b9035ea9b4",
      "name": "Khaman (dhokla)",
      "aiName": "Khaman dhokla",
      "quantity": 4.0, "unit": "piece", "grams": 120.0, "gramsSource": "ai",
      "source": "database",
      "match": {"foodCode": "BFP439", "foodName": "Khaman (dhokla)", "score": 1.0,
                "method": "fuzzy", "dataSource": "INDB 2024.11"},
      "nutrients": {"energy_kcal": 65.67, "protein_g": 2.31, "carb_g": 6.43, "fat_g": 3.36, "...": "38 keys"},
      "aiEstimate": {"energy_kcal": 192.0, "protein_g": 8.4, "carb_g": 24.0, "fat_g": 7.2},
      "confidence": 0.95,
      "needsReview": true,
      "reviewReasons": ["ai_db_disagree"],
      "reviewMessages": ["AI and database disagree a lot on calories: please check."],
      "alternatives": [{"foodCode": "ASC474", "foodName": "Dhokla"},
                       {"foodCode": "OSR115", "foodName": "Semolina dhokla (Suji/Rava dhokla)"}],
      "unitOptions": [{"unit": "piece", "gramsPerUnit": 30.0},
                      {"unit": "dhokla", "gramsPerUnit": 118.7},
                      {"unit": "g", "gramsPerUnit": 1}],
      "note": "", "edited": false
    },
    {"name": "Green chutney", "grams": 30.0, "source": "database",
     "match": {"foodCode": "OSR098", "method": "alias"}, "needsReview": false, "...": "..."}
  ],
  "totals": {"energy_kcal": 89.5, "protein_g": 3.94, "carb_g": 9.43, "fat_g": 3.84},
  "needsReview": true,
  "warnings": []
}
```
Time taken: **2.7–7 s for text**, **5–11 s for photos** (mostly the AI).

## B. "Is this correct?"

The app shows each item. Then:
- **Everything right?** → C (confirm).
- **Something wrong?** → `/fix`, as many times as needed. Each call returns the full updated analysis.

## C. Fix: `POST /api/auth/food/fix`

Every fix sends `analysisId` + `action` (+ `itemId`):

| Action | Body | What happens | Learning recorded |
|---|---|---|---|
| `use_estimate` | `itemId` | Switch this item to the AI's numbers | — (the data check was already queued) |
| `set_amount` | `itemId`, `quantity`+`unit` **or** `grams` | Recalculate nutrients for the new weight | **Personal portion**: "my katori = 200 g", kept only if within 0.2×–5× of the AI's size |
| `replace` | `itemId` + `foodCode` (picked) **or** `correctedName` (typed) | See "auto-fix" below; amount is kept | **Personal alias** (AI name → food) + shared candidate |
| `remove` | `itemId` | Delete the item | Removal logged |
| `add` | `correctedName` or `foodCode` (+ `grams`) | Add a dish the AI missed | Logged as `missed_item` |

### The auto-fix (`engine._resolve_correction`)
The user typed "no, these were methi thepla":
1. **Picked from the list** (`foodCode`)? Use that database food directly.
2. **Typed name clearly matches** the database (alias, or ≥ 0.86)? Use it.
3. **Otherwise ask the AI** (`correction_prompt`): "the AI thought Paratha, 2 piece, 100 g; the user says it's *no, these were methi thepla*". The AI returns one clean item ("Methi thepla", 2 piece, 100 g, estimate), which is then matched again → `OSR104 Methi thepla` ✓

Real result: 2 Plain paratha 298 kcal → **2 Methi thepla, 100 g, 346 kcal, ✓ Verified**.

## D. Confirm: `POST /api/auth/food/confirm`
```json
{"phoneNumber": "9876543210", "analysisId": "0cac90…", "mealType": "snack"}
```
- The analysis must belong to this user and still be `pending`. Confirming twice gives **409**.
- **Meal type:** what the user chose, otherwise guessed from the IST time of day:
  - 5–11 → breakfast
  - 11–16 → lunch
  - 16–19 → snack
  - otherwise → dinner
- A meal is saved in `food_meal_logs` with:
  - date (IST)
  - items (name, AI's first guess, amount, source, match, nutrients, edited)
  - totals
- The analysis becomes `confirmed`.

## E. Diary: `POST /api/auth/food/history`
`{"days": 7}` returns the meals grouped by IST date with daily totals, newest day first.

## F. Behind the scenes: learning
See [05](05-learning-and-fake-data.md). In short:
- corrections apply to the user **immediately**;
- they apply to everyone only after **3+ trusted users agree** and **a nutritionist approves**.

## Error responses (all routes)

| Code | When |
|---|---|
| 400 | Missing input, bad base64, unsupported type, amount outside 1–3000 g, unknown action/food code |
| 401 / 403 | No token / token doesn't match phone / not admin |
| 404 | Analysis, item or meal not found (or belongs to someone else) |
| 409 | Meal already saved |
| 413 | Photo over 6 MB or audio over 10 MB |
| 422 | Request format wrong (FastAPI validation), or a correction that isn't food |
| 429 | Too many scans |
| 502 | AI failed after retries |

Errors use FastAPI's normal `{"detail": "..."}`.

---

## AI-only mode (`FOOD_USE_DATABASE=false`)
This is the **current default**: nutrients come only from the AI. The database code is all still there; set `FOOD_USE_DATABASE=true` and restart to turn it back on.

| Part | `false` (AI only, default) | `true` (database + AI) |
|---|---|---|
| Analyze | 1 AI call; steps 4–5 skipped; every item `"source": "ai_estimate"`, `"match": null` | DB match + optional "choose" call, as above |
| Warnings | none for "not in database" (the estimate is expected) | "Not in our food database yet" etc. |
| AI vs DB check | not done; nothing goes to the data-conflict queue | flags > 2.5× differences |
| "Not this?" → pick from list | `alternatives` is empty; `POST /search` returns `[]`; `fix` with `foodCode` → **400** | works |
| "Not this?" → type a name | 1 AI call, AI estimate for the new name | DB first, AI if not found |
| Change amount / remove / add | same (AI estimate × grams) | same |
| Personal "my usual portion" | still learned, keyed by the dish name | keyed by food code |
| Response | `"databaseEnabled": false`, so the app hides the food-list picker | `"databaseEnabled": true` |

Corrections are still saved in `food_corrections`, so the training data keeps growing in both modes. The local server prints which mode it is in at start-up, and `/health` returns `useDatabase`.

---

## Privacy mode (`FOOD_STORE_DETAILS=false`)
This is the **current default**, agreed on 30 Oct 2026:
- no photos and no learning data are stored;
- the diary keeps dish names and 6 numbers for 30 days.

### What is stored, and for how long
| Data | Stored? | How long |
|---|---|---|
| Photo | never (only sent to Gemini) | — |
| Photo hash, typed text, voice transcript | never | — |
| **Scan in progress** (`food_analyses`): the item list so the user can fix it | yes | deleted when the user taps Save, or after **60 min** (`FOOD_PENDING_MINUTES`) |
| **Saved meal** (`food_meal_logs`) | dish name, amount, grams + **calories, protein, fat, fibre, sugar, sodium** per dish and in total | **30 days** (`FOOD_HISTORY_DAYS`) |
| Corrections, personal portions, trust, review queue | never | — |

A saved meal looks like this:
```json
{"userId": "9876543210", "mealType": "lunch", "date": "2026-10-30",
 "loggedAt": "2026-10-30T13:05:00+05:30",
 "items": [{"name": "Idli", "quantity": 3, "unit": "piece", "grams": 120,
            "nutrients": {"energy_kcal": 160, "protein_g": 5.1, "fat_g": 0.8, "fibre_g": 1.9,
                          "freesugar_g": 0.2, "sodium_mg": 210}}],
 "totals": {"energy_kcal": 250, "protein_g": 9.6, "...": "..."},
 "expireAt": "2026-11-29T00:00:00+05:30"}
```
Carbs are not kept, because they weren't on the agreed list. To add a number, put its key in `DIARY_NUTRIENTS` in `food/nutrients.py`.

### How the 30-day auto-delete works
- **`expireAt` = midnight IST, 30 days after the meal's date.** A meal from 30 Oct is shown through 28 Nov and gone on 29 Nov. The window is always today plus the previous 29 days.
- **MongoDB deletes it by itself.** The first time a meal or scan is saved, `MongoStore` creates a TTL index:
  ```javascript
  db.food_meal_logs.createIndex({ expireAt: 1 }, { expireAfterSeconds: 0 })   // same for food_analyses
  ```
  MongoDB checks about once a minute, and `expireAt` is stored as a real Date. No cron job is needed.
- **A second safety net:** both stores hide anything past `expireAt`, and `history()` never looks further back than 30 days. Even if MongoDB is a minute late, the user never sees day 31.
- **Locally**, `JsonFileStore` does the same: it hides expired records and removes them on the next write.

### What changes for the user
- Scanning, fixing, saving and the diary all work as before.
- Nothing is remembered between meals:
  - no "your usual portion";
  - no "based on your earlier correction";
  - no shared learning, and the nutritionist queue stays empty.
- The diary shows the last 30 days.
- Saving the same scan twice gives **404** (the scan was deleted on save), where it used to give 409.

### Account deletion
Call `await engine.delete_user_data(phone)` from your account-deletion flow. It immediately removes the user's documents from every `food_*` collection (in privacy mode that's only their meals and any open scan).

### Not covered by "we store nothing"
- **Google** may keep the photo for a limited time for abuse monitoring (paid tier). On the free tier it may be used to improve Google's products. Use a paid key and mention Gemini in the privacy policy.
- **Server logs:** make sure the server doesn't log request bodies; they contain the photo and the meal text.
- **Database backups:** they keep deleted meals until the backups themselves expire.

