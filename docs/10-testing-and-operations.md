# 10 · Testing, operations, costs, privacy and roadmap

## Setup on a new machine (Windows)
```powershell
cd Calories_Tracker
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          # then put your GEMINI_API_KEY in .env
.venv\Scripts\python scripts\build_food_db.py   # only if food\data\foods.json is missing
```
If `data/raw/` is empty, download INDB from the URL in [07](07-data-sources-and-quality.md) first.

## Running the test page
```powershell
.venv\Scripts\python main.py          # http://127.0.0.1:8100
.venv\Scripts\python main.py --lan    # prints http://<laptop-ip>:8100 for the iPhone
```
- **iPhone:** same Wi-Fi. If it doesn't load, allow Python through Windows Firewall (Private networks).
- **Each phone number** in the page is a separate test user, so you can try the multi-user learning yourself:
  1. three numbers correct the same dish;
  2. open the "Nutritionist queue" tab;
  3. approve.
- **Test data** lives in `local_store/*.json`. Delete the folder to start fresh.
- **API docs:** `http://127.0.0.1:8100/docs`.

### Suggested manual test list
The database rows below need `FOOD_USE_DATABASE=true` in `.env`. With the default `false`, every item shows ≈ AI estimate.

| Try | Expect |
|---|---|
| "2 phulka, 1 katori dal, aloo gobi" | 3 items; phulka ✓ via alias; aloo gobi ✓ via AI choice; dal ✓ "Mixed dal" |
| "4 khaman dhokla" | ✓ but ⚠ "AI and database disagree" → **Use AI estimate** works |
| "2 poori with aloo sabzi" | poori ≈ AI estimate ("Database value looked wrong") |
| "chicken biryani" | ≈ AI estimate (not in INDB), **not** chicken pulao |
| Photo of a thali | each dish separate; amounts editable |
| Photo of a laptop | "No food found" |
| "Not this?" → type "methi thepla" | replaced, same amount kept |
| Change amount: 3 piece | grams and nutrients scale |
| Same photo again after a fix | "Based on your earlier correction" |
| Save → My diary | meal and daily total shown |

## Automated tests
```powershell
.venv\Scripts\python -m pytest -q
```
- 37 tests, about 1.5 s, with **no AI cost** (`FakeAi` returns canned answers).
- They cover:
  - database build + suspect flags
  - matching (5 spellings)
  - parsing guards
  - all analyze paths: database / suspect / not found / AI choice / other-name double-check / disagreement / not food
  - every fix action
  - personal learning
  - 3-user consensus + approval
  - fake-user rejection
  - confirm, double-confirm, history
  - auth (401/403, admin)
  - rate limit (429 on the 11th call)
  - AI-only mode (`FOOD_USE_DATABASE=false`): no DB lookup, no choose call, food codes refused, search empty

**Run them after every change.** CI runs them on every push (`.github/workflows/ci.yml`).

## Measured performance (29 Oct 2026, gemini-3.5-flash-lite, home Wi-Fi)
| Input | Time |
|---|---|
| Text, 2–4 dishes | 2.7–7 s |
| Photo, 1–4 dishes | 4.8–10.7 s |
| Database matching (all items) | ~6 ms per name |
| Engine start-up | < 0.1 s |
| Fix with a picked food / amount change | instant (no AI) |
| Fix with typed name | 2–4 s (one AI call) |

## AI calls per meal (for cost planning)
| Step | Calls |
|---|---|
| Analyze | **1** (photo/text/voice) + **0–1** "choose" call (text only, small) when a match is unsure |
| Fix: picked food, amount, remove, use estimate | 0 |
| Fix: typed name not found directly | 1 (text only, small) |

Typically **1–2 calls per logged meal**. Check current prices at `ai.google.dev/pricing` (Flash-Lite is Google's cheapest tier) and multiply by expected meals per day. Use the **paid tier** in production: on the free tier Google may use the data to improve its products, and human reviewers may read it. That isn't acceptable for users' health data.

## Limits and guards (defaults)
| Guard | Value | Setting |
|---|---|---|
| Scans per user | 10 / minute, 150 / day | `FOOD_RATE_LIMIT_PER_MINUTE`, `_PER_DAY` |
| Photo size | 6 MB | `FOOD_MAX_IMAGE_BYTES` |
| Audio size | 10 MB | `FOOD_MAX_AUDIO_BYTES` |
| Items per meal | 15 | `FOOD_MAX_ITEMS` |
| Text length | 1,000 chars | `schemas.py` |
| Grams per item | 1–3,000 | `parsing.py`, `schemas.py` |
| Energy density | ≤ 9 kcal/g | `parsing.py` |
| AI timeout | 60 s, retries 1/2/4 s | `FOOD_AI_TIMEOUT_SECONDS` |

## Security checklist
- [x] Gemini key only on the server (env / Secrets Manager), never in the app
- [x] Every app route checks that the token matches the phone (signed HS256 token)
- [x] Users can only read or change their own analyses and meals (404 otherwise)
- [x] Admin routes need a admin role
- [x] Rate limit on AI-calling routes
- [x] Input size and type limits; base64 validated
- [x] User text can't change the AI's instructions; AI output is validated
- [ ] **Deploy check:** `FOOD_AUTH_MODE` is **not** `dev`
- [ ] Use a long random `FOOD_AUTH_SECRET` and a fresh Gemini key for production

## Privacy (India's DPDP Act 2023)
> **Current setting (30 Oct 2026): privacy mode** (`FOOD_STORE_DETAILS=false`). Only dish names and 6 nutrients per meal are kept, for 30 days; there is no learning data. Retention and account deletion are built in. Full details are in [03](03-request-flow.md#privacy-mode-food_store_detailsfalse). The notes below apply if learning is turned back on.

- **Photos are not stored**: only a SHA-256 hash.
- **Stored:** meal text, AI results, corrections and the diary, all linked to the phone number. Food diaries are **health-related personal data**.
- **Before launch:**
  1. Add food logging to the privacy policy and in-app consent (what's stored, why, for how long).
  2. **Account deletion:** delete the user's documents from all `food_*` collections, *not built yet*. Call `engine.delete_user_data(userId)` from your account-deletion flow (`userId` = normalised phone).
  3. **Retention:** unconfirmed `food_analyses` aren't needed after a day. Add a nightly clean-up or a Mongo TTL index (this needs `createdAt` stored as a Date instead of text; small change in `engine.py`).
  4. Corrections used for shared learning are kept without photos. Exported training data should drop phone numbers (the export script already leaves them out).

## Known limitations
| Limitation | Effect | Fix |
|---|---|---|
| INDB lacks raw foods (fruit, curd, milk, eggs) | Those use AI estimates | Add IFCT / USDA ([07](07-data-sources-and-quality.md)) |
| 118 suspect INDB rows + a few wrong rows | AI estimate used; flagged | Nutritionist clean-up |
| Photo portion sizes are estimates | ±20–30 % typical | User edits; "your usual" portions improve it per user |
| Shared plates (e.g. a platter for 4) | Counts the whole plate (12 idlis on a platter) | User changes the amount; could add a "how many people shared?" question |
| Cooking oil varies by household | Estimates assume moderate oil | Possible "less / normal / more oil" toggle |
| In-memory rate limit is per process | Limit × number of processes | Redis limiter |
| Bedrock code not executed here | May need small fixes | Test before enabling |

## Roadmap (suggested order)
1. **Now:** deploy (doc 08), run the manual test list on real phones, and get the INDB licence email out.
2. **Weeks 1–2:**
   - nutritionist cleans the 118 suspect rows and reviews the queue;
   - add ~100 more aliases from real usage;
   - add IFCT raw foods.
3. **Weeks 3–4:**
   - "Recent & favourites" for one-tap re-logging (Indian home food repeats, so this saves AI calls);
   - oil toggle;
   - connect the diary to an AI coach / goals feature.
4. **Month 2:**
   - review-queue screen in the admin app;
   - monthly prompt tuning from `food_corrections`;
   - a 50–100 meal **test set with weighed food**, run after every change to track accuracy.
5. **Month 3+:**
   - consented photo collection;
   - train your own Indian-dish recognition model in Colab (`nutrition/food-model/`) using `export_training_data.py`, as a cheaper first step before Gemini.
