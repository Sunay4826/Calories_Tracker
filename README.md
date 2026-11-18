# Calories Tracker

[![CI](https://github.com/Sunay4826/Calories_Tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/Sunay4826/Calories_Tracker/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688) ![Gemini](https://img.shields.io/badge/LLM-Gemini-orange)

**AI meal logging for Indian food.** Log a meal by **photo, text or voice**; an LLM identifies the dishes and portions, and the nutrients come from a real Indian food database, with a review step so the user can fix anything before it's saved.

A user logs a meal and the engine:

1. asks **Gemini** what dishes are in it and how much (structured JSON output),
2. looks each dish up in the **INDB Indian food database** (1,014 dishes, 38 nutrients),
3. calculates nutrients for the actual **weight** (per 100 g × grams),
4. falls back to **Gemini's own estimate** when a dish is missing or the database value looks wrong,
5. asks the user **"Is this correct?"** and lets them fix anything,
6. **learns** from fixes: instantly for that user, and for everyone only after several users agree **and** a nutritionist approves (so fake corrections can't poison the data).

## Highlights

- **Repeatable LLM output:** temperature 0, a fixed seed and photo-counting prompt rules, plus JSON repair and retries for malformed replies.
- **Grounded numbers:** nutrients come from INDB, not the model; 118 unreliable database rows are flagged and the AI estimate is used instead.
- **Privacy-first:** photos are never stored (only a SHA-256 hash); meals keep only dish names and 6 nutrients and auto-delete after 30 days (MongoDB TTL).
- **Abuse-resistant learning:** per-user trust scores, consensus across users and a nutritionist review queue before any shared change.
- **Production basics:** signed JWT auth, per-user rate limits, input validation, prompt-injection guards, 42 automated tests, CI and a Dockerfile.

## Architecture

```
 📷 Photo ─┐
 ✍️ Text  ─┼─► client (web / mobile) ──► POST /api/auth/food/analyze
 🎤 Voice ─┘   (speech-to-text            │
               on the device)             ▼
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

**Stack:** Python, FastAPI, Gemini API (optional Claude on AWS Bedrock), MongoDB (motor), pytest, Docker, GitHub Actions.

## Quick start

```bash
git clone https://github.com/Sunay4826/Calories_Tracker.git
cd Calories_Tracker
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # add your GEMINI_API_KEY
python main.py                                        # http://127.0.0.1:8100  (API docs: /docs)
python -m pytest -q                                   # run the 42 tests
```

Locally it uses fake `dev-<phone>` tokens and JSON files, so no database is needed. For production (signed tokens + MongoDB + Docker) see [docs/08-deployment.md](docs/08-deployment.md).

## Status

| Part | State |
|---|---|
| Engine (`food/`) | ✅ 42 automated tests pass; tested live with Gemini on text and photos |
| Web client (`web/index.html`) | ✅ Log meal (photo/text/voice), diary, nutritionist queue |
| Signed-token auth + MongoDB store | ✅ Built and unit-tested |
| Bedrock (Claude) option | ⚠️ Written, not yet run against AWS |
| INDB licence | ⚠️ "Open access", no licence stated; ask Anuvaad before commercial use |

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
| 08 | [Deployment](docs/08-deployment.md) | Docker, environment variables, MongoDB, login tokens |
| 09 | [Voice and text](docs/09-voice-and-text.md) | Speech-to-text options, free tools, languages |
| 10 | [Testing and operations](docs/10-testing-and-operations.md) | Setup, tests, costs, limits, privacy, security, roadmap |
