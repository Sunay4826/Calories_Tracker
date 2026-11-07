# Calories Tracker

A user logs a meal by **photo, text or voice**. The engine:

1. asks **Gemini** what dishes are in it and how much (as JSON),
2. looks each dish up in the **INDB Indian food database** (1,014 dishes, 38 nutrients),
3. calculates nutrients for the user's **weight** (per 100 g × grams),
4. falls back to **Gemini's own estimate** when a dish is missing or the database value looks wrong,
5. asks the user **"Is this correct?"** and lets them fix anything,
6. **learns** from fixes: instantly for that user, and for everyone only after several users agree **and** a nutritionist approves (so fake corrections can't poison the data).

It's built as one self-contained Python folder (`food/`) that drops into **host-backend** as `app/services/food/`, plus Flutter screens that drop into the **host app** as `lib/features/food_log/`.

```
 📷 Photo ─┐
 ✍️ Text  ─┼─► host app ──► host-backend /api/auth/food/analyze
 🎤 Voice ─┘   (phone does         │
               speech-to-text)     ▼
                           ① Gemini: dishes + grams + its own estimate
                           ② Match each dish in INDB (aliases → fuzzy → AI double-check)
                           ③ Nutrients = database per 100 g × grams  (✓ Verified)
                              or Gemini estimate                      (≈ AI estimate)
                           ④ Flag anything doubtful
                                   │
               "Is this correct?" ◄┘
               ├─ ✓ Save          → /confirm → food diary
               └─ ✗ Fix           → /fix → recalculated + learning
                                          ├─ personal: applies at once, only for this user
                                          └─ shared: 3+ users agree → nutritionist approves → everyone
```

## Status (29 Oct 2026)

| Part | State |
|---|---|
| Engine (`food/`) | ✅ Built, **24 automated tests pass**, tested live with Gemini on text and photos |
| Test page (`web/index.html`) | ✅ Works on laptop and iPhone (same Wi-Fi) |
| Flutter screens (`flutter/`) | ⚠️ Written in the host app's style but **not compiled**: no Flutter SDK on this laptop. Run `flutter analyze` after copying |
| Bedrock (Claude) option | ⚠️ Written, **not run**: needs AWS credentials. Test on dev before using |
| MongoDB store | ⚠️ Written for the host app's `get_allowed_collection`, **not run** here (local uses JSON files) |
| INDB licence | ⚠️ "Open access", but no licence stated. **Get written permission from Anuvaad before production** |

## Quick start (local testing)

```powershell
cd Calories_Tracker
.venv\Scripts\python main.py            # laptop: http://127.0.0.1:8100
.venv\Scripts\python main.py --lan      # also prints a URL for your iPhone (same Wi-Fi)
.venv\Scripts\python -m pytest -q       # run the 37 tests
```

First-time setup on a new machine: [docs/10-testing-and-operations.md](docs/10-testing-and-operations.md).

## Documentation

| # | Document | What's in it |
|---|---|---|
| 01 | [Overview](docs/01-overview.md) | What it does, why it's built this way, key decisions |
| 02 | [Every file explained](docs/02-files.md) | What each file does and when it runs |
| 03 | [Request flow, step by step](docs/03-request-flow.md) | Exactly what happens on analyze / fix / confirm, with real JSON |
| 04 | [Matching and nutrient maths](docs/04-matching-and-nutrients.md) | How "Khaman dhokla" becomes INDB `BFP439`; per-100 g × grams; when AI estimates are used |
| 05 | [Learning and fake data](docs/05-learning-and-fake-data.md) | Personal vs global learning, trust scores, review queue, retraining |
| 06 | [API reference](docs/06-api-reference.md) | Every route, every field, every error |
| 07 | [Data sources and quality](docs/07-data-sources-and-quality.md) | INDB, the 118 flagged dishes, licences, adding more data |
| 08 | [Backend integration](docs/08-backend-integration.md) | Exact steps for the backend, the Flutter app and AWS |
| 09 | [Voice and text](docs/09-voice-and-text.md) | Speech-to-text options, free tools, languages |
| 10 | [Testing and operations](docs/10-testing-and-operations.md) | Setup, tests, costs, limits, privacy, security, roadmap |
