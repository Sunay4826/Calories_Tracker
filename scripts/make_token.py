"""Create a signed login token for FOOD_AUTH_MODE=jwt.

    python scripts/make_token.py 9876543210            # user token
    python scripts/make_token.py admin --admin          # nutritionist/admin token

Reads FOOD_AUTH_SECRET from the environment (or .env).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from main import load_env_file  # noqa: E402

load_env_file(Path(__file__).resolve().parent.parent / ".env")
from food.auth import make_token  # noqa: E402

if len(sys.argv) < 2:
    sys.exit(__doc__)
secret = os.environ.get("FOOD_AUTH_SECRET", "")
if not secret:
    sys.exit("Set FOOD_AUTH_SECRET first.")
print(make_token(sys.argv[1], secret, role="admin" if "--admin" in sys.argv else "user"))
