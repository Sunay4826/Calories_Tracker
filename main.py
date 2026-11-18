"""Calories Tracker API server.

    .venv\\Scripts\\python main.py          -> http://127.0.0.1:8100 (this laptop)
    .venv\\Scripts\\python main.py --lan    -> also http://<laptop-ip>:8100 for the iPhone

Defaults are for local testing: FOOD_AUTH_MODE=dev (fake "dev-<phone>" tokens)
and FOOD_STORE=json (files in local_store/ instead of MongoDB). For production set
FOOD_AUTH_MODE=jwt and FOOD_STORE=mongo (see docs/08-deployment.md).
The test page is web/index.html.
"""
import os
import socket
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() and not name.strip().startswith("#"):
            os.environ.setdefault(name.strip(), value.strip().strip('"'))


load_env_file(HERE / ".env")
os.environ.setdefault("FOOD_AUTH_MODE", "dev")
os.environ.setdefault("FOOD_STORE", "json")
os.environ.setdefault("FOOD_JSON_STORE_DIR", str(HERE / "local_store"))

from fastapi import FastAPI, HTTPException, status  # noqa: E402  (env must be set first)
from fastapi.responses import FileResponse  # noqa: E402

from food.api import admin_router, router  # noqa: E402
from food.config import get_food_settings  # noqa: E402

if get_food_settings().auth_mode == "dev":
    print("NOTE: FOOD_AUTH_MODE=dev accepts fake tokens. Use FOOD_AUTH_MODE=jwt in production.")

app = FastAPI(
    title="Calories Tracker API",
    description="Log Indian meals by photo, text or voice. An LLM extracts dishes and portions as JSON; "
                "nutrients come from the INDB database with an AI estimate as fallback.",
    version="1.0.0",
)
app.include_router(router)
app.include_router(admin_router)


@app.get("/", include_in_schema=False)
def test_page() -> FileResponse:
    return FileResponse(HERE / "web" / "index.html")


@app.get("/api/demo/token", include_in_schema=False)
def demo_token(phone: str, admin: bool = False) -> dict:
    """Signed tokens for the public demo page. Off unless FOOD_DEMO_LOGIN=true."""
    s = get_food_settings()
    if s.auth_mode != "jwt" or not s.demo_login:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    from food.auth import make_token

    return {"token": make_token("demo-admin" if admin else phone, s.auth_secret,
                                role="admin" if admin else "user", ttl_seconds=3600)}


@app.get("/health")
def health() -> dict:
    s = get_food_settings()
    return {"ok": True, "aiProvider": s.ai_provider, "model": s.gemini_model, "hasKey": bool(s.gemini_api_key),
            "useDatabase": s.use_database, "storeDetails": s.store_details, "historyDays": s.history_days}


def lan_ip() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "your-laptop-ip"


if __name__ == "__main__":
    import uvicorn

    lan = "--lan" in sys.argv
    print("Food engine test page: http://127.0.0.1:8100   (API docs: /docs)")
    if lan:
        print(f"On your iPhone (same Wi-Fi): http://{lan_ip()}:8100")
    print("Food database: " + ("ON (INDB lookup, AI estimate as fallback)" if get_food_settings().use_database
                               else "OFF (AI estimates only; set FOOD_USE_DATABASE=true to turn on)"))
    print("Privacy: " + ("FULL DETAILS (learning on)" if get_food_settings().store_details else
                         f"numbers + dish names only, kept {get_food_settings().history_days} days; no learning"))
    if not get_food_settings().gemini_api_key:
        print("WARNING: GEMINI_API_KEY missing - add it to .env")
    host = os.environ.get("HOST", "0.0.0.0" if lan else "127.0.0.1")
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", 8100)))
