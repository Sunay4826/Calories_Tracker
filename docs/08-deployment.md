# 08 · Deployment

The service is one FastAPI app (`main.py`) that runs anywhere Docker or Python 3.10+ runs.

## Production settings

| Variable | Value | Notes |
|---|---|---|
| `GEMINI_API_KEY` | your key | from aistudio.google.com/apikey |
| `FOOD_AUTH_MODE` | `jwt` | never `dev` in production |
| `FOOD_AUTH_SECRET` | long random string | signs login tokens (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) |
| `FOOD_STORE` | `mongo` | |
| `MONGODB_URI` | e.g. a MongoDB Atlas connection string | |
| `MONGODB_DB` | `calories_tracker` | |
| `PORT` | set by your host | defaults to 8100 |
| `HOST` | `0.0.0.0` | the Docker image sets this |

Every other setting has a safe default; see `.env.example`.

## Run with Docker

```bash
docker build -t calories-tracker .
docker run -p 8100:8100 --env-file .env calories-tracker
```

Open `http://localhost:8100` for the web client and `/docs` for the interactive API docs.

## Deploy to Render / Railway / Fly.io

1. Create a new web service from this repository (it picks up the `Dockerfile`).
2. Add the environment variables above.
3. Health check path: `/health`.

## MongoDB collections

The service creates these on first use, plus TTL indexes on `expireAt` so old data deletes itself:

`food_analyses`, `food_meal_logs`, `food_corrections`, `food_user_profiles`, `food_aliases`, `food_review_queue`, `food_overrides`.

## Login tokens

In `jwt` mode every request needs `Authorization: Bearer <token>`. Create one with:

```bash
FOOD_AUTH_SECRET=... python scripts/make_token.py 9876543210         # user
FOOD_AUTH_SECRET=... python scripts/make_token.py nutritionist --admin  # admin
```

A real app would issue these from its own login service using the same secret (`food.auth.make_token`).

## Account deletion

Call `await engine.delete_user_data(user_id)` when a user deletes their account. It removes their documents from every `food_*` collection.

## Scaling notes

- `food/ratelimit.py` counts per process. With several workers, move the counters to Redis for exact limits.
- `FOOD_AI_PROVIDER=bedrock` switches photo/text analysis to Claude on AWS Bedrock (needs `boto3` and AWS credentials).
