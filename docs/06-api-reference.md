# 06 · API reference

**Base URL:** the host backend (`AppConfig.backendBaseUrl` in the app). Locally: `http://127.0.0.1:8100`, with interactive docs at `/docs`.

**App routes**
- All are `POST` with a JSON body containing `phoneNumber`.
- Header: `Authorization: Bearer <app token>` (locally: `Bearer dev-<phone>`).
- Errors come back as `{"detail": "message"}`. Codes are listed at the end of [03](03-request-flow.md).

---

## `POST /api/auth/food/analyze`
Recognise a meal and calculate nutrients. **Calls the AI · rate-limited.**

| Field | Type | Required | Notes |
|---|---|---|---|
| `phoneNumber` | string | ✓ | must match the token |
| `text` | string ≤ 1000 | one of text/image/audio | description, or speech converted to text on the phone |
| `imageBase64` | string | | JPEG/PNG/WebP/HEIC, ≤ 6 MB decoded. Resize to ~1280 px first |
| `imageMimeType` | string | | default `image/jpeg` |
| `audioBase64` | string | | voice note, ≤ 10 MB, **Gemini only**. Prefer on-phone speech-to-text |
| `audioMimeType` | string | | e.g. `audio/aac`, `audio/mp4`, `audio/wav`, `audio/mpeg`, `audio/ogg`, `audio/flac` |
| `mealType` | `breakfast`/`lunch`/`dinner`/`snack` | | stored on the analysis |

**Response** (full example in [03](03-request-flow.md)):

| Field | Meaning |
|---|---|
| `analysisId` | use for fix/confirm. `null` when `notFood` |
| `notFood`, `message` | no food found → show `message` |
| `status` | `pending` until confirmed |
| `inputKind` | e.g. `["photo","text"]` |
| `transcript` | what the AI heard (audio input only) |
| `items[]` | see below |
| `totals` | sum of all items' nutrients |
| `needsReview` | any item flagged |
| `warnings[]` | technical notes (e.g. "Only the first 15 items were kept") |

**Item fields:**

| Field | Meaning |
|---|---|
| `itemId` | use for fix |
| `name` | what to show (database name if matched, else AI name) |
| `aiName` | what the AI called it |
| `originalAiName` | after a replace: the AI's first (wrong) guess |
| `quantity`, `unit`, `grams` | amount |
| `gramsSource` | `ai`, `your_usual`, `user`, `database_serving`, `default` |
| `source` | `database` (✓ Verified) · `ai_estimate` (≈) · `none` |
| `match` | `{foodCode, foodName, score, method, dataSource}` or `null`; `method` = `alias`, `personal_alias`, `fuzzy`, `ai_choice`, `user_choice` |
| `dataQuality` | `ok` / `suspect` (matched items only) |
| `nutrients` | up to 38 keys, for `grams` |
| `aiEstimate` | the AI's numbers for the same grams, when `source` is `database` (for comparison) |
| `confidence` | AI's 0–1 certainty about the dish |
| `needsReview`, `reviewReasons[]`, `reviewMessages[]` | reasons: `not_in_database`, `database_value_unreliable`, `ai_db_disagree`, `low_confidence`, `no_nutrient_data`, `default_amount` |
| `note` | e.g. "Based on your earlier correction." |
| `alternatives[]` | `{foodCode, foodName}`: "Not this? Maybe…" from the database |
| `aiAlternatives[]` | the AI's look-alike guesses (names only) |
| `unitOptions[]` | `{unit, gramsPerUnit, yours?}` |
| `edited` | the user changed this item |

---

## `POST /api/auth/food/fix`
Change one item and get the whole updated analysis back. Only for `pending` analyses.

| Field | Used by | Notes |
|---|---|---|
| `analysisId` | all | |
| `action` | all | `replace`, `set_amount`, `remove`, `add`, `use_estimate` |
| `itemId` | all except `add` | |
| `foodCode` | replace, add | picked from `alternatives` or `/search` (no AI call) |
| `correctedName` | replace, add | typed text, ≤ 120 chars (may call the AI, so it's rate-limited) |
| `grams` | set_amount, replace, add | 1–3000 |
| `quantity` + `unit` | set_amount | `unit` must be one of the item's `unitOptions` |

Examples:
```json
{"phoneNumber":"98…","analysisId":"…","action":"set_amount","itemId":"a5b9…","quantity":3,"unit":"piece"}
{"phoneNumber":"98…","analysisId":"…","action":"replace","itemId":"a5b9…","foodCode":"ASC474"}
{"phoneNumber":"98…","analysisId":"…","action":"replace","itemId":"a5b9…","correctedName":"it was nylon khaman"}
{"phoneNumber":"98…","analysisId":"…","action":"add","correctedName":"1 glass chaas"}
{"phoneNumber":"98…","analysisId":"…","action":"use_estimate","itemId":"a5b9…"}
{"phoneNumber":"98…","analysisId":"…","action":"remove","itemId":"a5b9…"}
```

## `POST /api/auth/food/confirm`
Save to the diary. Body: `analysisId`, optional `mealType`, optional `loggedAt` (ISO datetime; default now).
Response: `{"meal": {id, analysisId, mealType, loggedAt, date, inputKind, items[], totals}}`. Confirming twice gives **409**.

## `POST /api/auth/food/search`
Database search for the "Not this?" screen. Body: `query` (2–80 chars), `limit` (1–25, default 10).
Response: `{"results": [{foodCode, foodName, score, kcalPer100g, serving, quality}]}`.

## `POST /api/auth/food/history`
Body: `date` (`YYYY-MM-DD`, last day, IST, default today), `days` (1–31, default 1).
Response: `{"from", "to", "days": [{"date", "meals": [meal…], "totals"}]}`, newest day first.

## `POST /api/auth/food/meals/delete`
Body: `mealId`. Response `{"deleted": true}`. Only the owner can delete.

---

## Admin (nutritionist) routes
Header: an **admin** token (roles admin / internal_admin / superadmin). Locally: `Bearer dev-admin`.

### `GET /api/admin/food/review-queue?status=pending`
`status` is one of `pending` · `approved` · `rejected`. Returns `{"items": [...]}`:
- **`type: "alias"`** — `{id: "alias:<key>", aiName, foodCode, foodName, supporters[], usersWhoCorrected, agreement}`
- **`type: "data_conflict"`** — `{id: "data:<code>", foodCode, foodName, dbKcalPer100g, aiKcalSamples[], count}`

### `POST /api/admin/food/review/{reviewId}`
Body: `{"decision": "approve" | "reject", "note": "optional, ≤ 300 chars"}`. URL-encode the id (it contains `:`).

| Type | approve | reject |
|---|---|---|
| alias | global alias created; supporters +0.1 trust | supporters −0.2 trust |
| data_conflict | DB row marked unreliable → AI estimate for everyone | DB value kept |

There's no admin UI in the admin app yet. The local test page has a "Nutritionist queue" tab showing what it should look like.
