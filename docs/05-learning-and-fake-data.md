# 05 · Learning from corrections, and protecting against fake data

## The problem
Users will correct the engine, and that's how it improves. But some corrections will be **wrong** (honest mistakes) or **fake** (trolls, competitors, someone testing). If one user could teach "gulab jamun = Mayonnaise" for everyone, the data would be ruined.

**Rule: a correction always helps the person who made it at once, but reaches everyone only after several trusted people agree AND a nutritionist approves.**

## Three levels

```
                ┌──────────────────────────────────────────────────────────┐
 User fixes ───►│ LEVEL 1 · PERSONAL           applies immediately, only  │
 an item        │  • name fix  → personal alias   for this user           │
                │  • amount    → personal portion                         │
                └───────────────┬──────────────────────────────────────────┘
                                │ only if: trust ≥ 0.3, daily quota left,
                                │ actually changed the food, is a name fix
                                ▼
                ┌──────────────────────────────────────────────────────────┐
                │ LEVEL 2 · CANDIDATE          waits for agreement        │
                │  ≥ 3 different trusted users map the same AI name to    │
                │  the same food, and they are ≥ 70 % of everyone who     │
                │  corrected that name                                     │
                └───────────────┬──────────────────────────────────────────┘
                                ▼
                ┌──────────────────────────────────────────────────────────┐
                │ NUTRITIONIST REVIEW QUEUE    /api/admin/food/review-queue│
                │  approve → LEVEL 3 · GLOBAL alias, used for everyone    │
                │  reject  → nothing changes, supporters lose trust       │
                └──────────────────────────────────────────────────────────┘
```

### Level 1 · Personal (instant)
| User does | Saved in their profile | Next time |
|---|---|---|
| "Not this?" → picks Moong dal for the AI's "Yellow dal" | `aliases["yelov dal"] = ASC151` | Photo-only scans: "Yellow dal" → Moong dal, with the note "Based on your earlier correction" |
| Changes 2 roti from 80 g to 60 g | `portions["ASC096"]["roti"] = 30` | Their rotis are 30 g each (`gramsSource: your_usual`) |

**Safety rules for personal learning:**
- Personal name fixes apply only to **photo-only** scans. If the user types or says "paratha", we believe them. (Found in testing: one correction would otherwise turn every future typed "paratha" into thepla.)
- A personal portion is saved only if it's within **0.2×–5×** of the AI's size per unit. "1 roti = 2 kg" is used for that meal but never remembered.
- One user's personal data never affects another user.

### Level 2 · Candidate (agreement)
A name correction becomes a **candidate** only if all of these hold:
- the user's **trust ≥ 0.3** (`FOOD_MIN_TRUST_FOR_GLOBAL`);
- they have sent **fewer than 30** shared corrections today (`FOOD_MAX_GLOBAL_CORRECTIONS_PER_DAY`), which stops bulk spam;
- it's a name fix (`replace` with a resulting database food);
- it actually changed the food (not re-confirming the same one).

After each candidate, `_check_consensus()` counts, for that AI name:
- **the latest correction per user** (one user = one vote, however many times they repeat it);
- only users who are **still trusted**;
- the most popular target food.

If ≥ **3 users** (`FOOD_PROMOTE_MIN_USERS`) chose it **and** they are ≥ **70 %** (`FOOD_PROMOTE_MIN_AGREEMENT`) of the trusted users who corrected that name, a review item `alias:<name>` is created.

### Level 3 · Global (nutritionist approved)
In the queue the nutritionist sees:
> **Name fix:** "Kaman" → **Dhokla** · 3 of 3 users agree (100 %) · [Approve] [Reject]

- **Approve:**
  - a global alias is saved in `food_aliases` and applies to every user within 60 s (cache);
  - each supporter's trust **+0.1** (max 1.0);
  - their corrections are marked `approved`.
- **Reject:**
  - nothing global changes;
  - each supporter's trust **−0.2** (min 0);
  - their corrections are marked `rejected`.

`FOOD_AUTO_PROMOTE=true` skips the nutritionist. **Not recommended**: agreement alone can be faked with a few accounts.

## Trust score

| Event | Trust |
|---|---|
| New user | 0.5 |
| Suggestion approved | +0.1 |
| Suggestion rejected | −0.2 |
| Below 0.3 | Corrections stay personal only (still work for them) |

Example: a user who pushes fake corrections gets rejected twice: 0.5 → 0.3 → 0.1. Their corrections are now ignored for everyone, while their own app still works normally.

## Data checks: when the database itself is wrong
Sometimes the user and AI are right and **INDB is wrong**. If the database and AI calories for a dish differ by more than **2.5×**:
- the item is flagged for the user ("AI and database disagree a lot…"), with a **Use AI estimate** button;
- a review item `data:<code>` is created or updated, with the database value, the last 10 AI values and a count.

> **Data check:** Khaman (dhokla) · database 55 kcal/100 g vs AI 160, 160 · seen 2× · [Mark DB value wrong] [DB is fine]

**Mark DB value wrong** saves an override in `food_overrides`. From then on that row is treated as "suspect" and every user gets the AI estimate for it, until someone fixes the data (see [07](07-data-sources-and-quality.md)).

The two data checks that came up during testing were Khaman dhokla (55 vs ~160–190 kcal/100 g) and Hot tea (16 vs ~55: INDB's tea is likely without sugar or much milk).

## What is stored (audit trail)
Every correction is a document in `food_corrections`:
```json
{"id": "…", "userId": "9876543210", "kind": "identity",
 "aiName": "Paratha", "aiKey": "parata", "originalCode": "ASC097",
 "foodCode": "OSR104", "correctedText": "no, these were methi thepla",
 "analysisId": "…", "status": "candidate", "createdAt": "2026-10-29T…"}
```
- `kind` is one of `identity`, `missed_item`, `portion` or `remove`.
- `status` is one of `personal`, `candidate`, `approved` or `rejected`.

So you can always answer "who taught the system this, and when?"

## "Retraining": what actually improves

| What improves | How | When |
|---|---|---|
| **Name matching** for that user | personal aliases | instantly |
| **Portion sizes** for that user | personal portions | instantly |
| **Name matching for everyone** | approved global aliases | after nutritionist approval (≤ 60 s cache) |
| **Wrong database rows** | overrides → AI estimate, then fix the data | after nutritionist decision |
| **Prompts** | read `food_corrections` monthly: frequent mistakes → add a hint to `prompts.py` | manual, monthly |
| **Your own model** (Colab) | `scripts/export_training_data.py` → train | later, once you have thousands of trusted rows |

Gemini itself can't be retrained by you. The engine improves *around* it.

### Exporting training data
```powershell
.venv\Scripts\python scripts\export_training_data.py
```
Writes `training_data.jsonl`, one line per confirmed item:
```json
{"input": "text", "userText": "2 paratha with a bowl of curd", "aiName": "Paratha",
 "label": "OSR104", "labelName": "Methi thepla", "grams": 150.0,
 "userEdited": true, "trusted": true}
```
- **`trusted`:** the label is a database food and the user's trust is ≥ 0.5. **Train only on trusted rows**; have a nutritionist sample-check the rest.
- **Photos:** the engine does **not** keep photos (only a hash). Training a photo model needs a separate feature: an explicit consent checkbox ("help improve food recognition") that uploads the photo to S3 (The host app already has `app/core/s3.py`). Build that only with legal review under the DPDP Act.

## Tests that prove this works
In `tests/test_engine.py`:
- `test_set_amount_remembers_personal_portion`
- `test_replace_with_food_code_creates_personal_alias`: also checks typed names are believed and other users are unaffected
- `test_global_alias_needs_three_users_and_nutritionist`: 2 users are not enough; 3 users create a pending item; global only after approval; trust goes to 0.6
- `test_rejected_fake_corrections_lower_trust_and_stop_sharing`: rejection gives trust 0.3; a low-trust user's corrections stay personal
- `test_big_disagreement_flags_review_and_queues_data_check`
- `test_use_ai_estimate_instead_of_database`
