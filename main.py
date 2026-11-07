"""Local test server for the food engine. NOT used inside host-backend.

    .venv\\Scripts\\python main.py          -> http://127.0.0.1:8100 (this laptop)
    .venv\\Scripts\\python main.py --lan    -> also http://<laptop-ip>:8100 for the iPhone

It runs the same food/ module the host app will run, with two test-only switches:
FOOD_AUTH_MODE=dev (fake "dev-<phone>" tokens) and FOOD_STORE=json (files in
local_store/ instead of MongoDB). The test page is web/index.html.
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

from fastapi import FastAPI  # noqa: E402  (env must be set first)
from fastapi.responses import FileResponse  # noqa: E402

from food.api import admin_router, router  # noqa: E402
from food.config import get_food_settings  # noqa: E402

if get_food_settings().auth_mode != "dev" or get_food_settings().store != "json":
    print("main.py is for local testing: it expects FOOD_AUTH_MODE=dev and FOOD_STORE=json.")

app = FastAPI(title="Calories Tracker (local test)")
app.include_router(router)
app.include_router(admin_router)


@app.get("/", include_in_schema=False)
def test_page() -> FileResponse:
    return FileResponse(HERE / "web" / "index.html")


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
        print("WARNING: GEMINI_API_KEY missing - add it to food-service/.env")
    uvicorn.run(app, host="0.0.0.0" if lan else "127.0.0.1", port=8100)
