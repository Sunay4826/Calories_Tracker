"""Signed-token auth (FOOD_AUTH_MODE=jwt)."""
import asyncio
import time
from dataclasses import replace

import pytest
from fastapi import HTTPException

from food import auth
from food.config import get_food_settings

SECRET = "test-secret-with-enough-length"


@pytest.fixture
def jwt_mode(monkeypatch):
    settings = replace(get_food_settings(), auth_mode="jwt", auth_secret=SECRET)
    monkeypatch.setattr(auth, "get_food_settings", lambda: settings)


def run(coro):
    return asyncio.run(coro)


def test_valid_user_token(jwt_mode):
    token = auth.make_token("+91 98765 43210", SECRET)
    assert run(auth.verify_app_user("9876543210", f"Bearer {token}")) == "9876543210"


def test_token_for_other_user_is_forbidden(jwt_mode):
    token = auth.make_token("9876543210", SECRET)
    with pytest.raises(HTTPException) as exc:
        run(auth.verify_app_user("9123456780", f"Bearer {token}"))
    assert exc.value.status_code == 403


def test_tampered_or_wrong_secret_is_rejected(jwt_mode):
    token = auth.make_token("9876543210", "another-secret")
    with pytest.raises(HTTPException) as exc:
        run(auth.verify_app_user("9876543210", f"Bearer {token}"))
    assert exc.value.status_code == 401


def test_expired_token_is_rejected(jwt_mode):
    token = auth.make_token("9876543210", SECRET, ttl_seconds=-1)
    with pytest.raises(HTTPException) as exc:
        run(auth.verify_app_user("9876543210", f"Bearer {token}"))
    assert exc.value.status_code == 401


def test_admin_route_needs_admin_role(jwt_mode):
    user = auth.make_token("9876543210", SECRET)
    admin = auth.make_token("nutritionist", SECRET, role="admin")
    with pytest.raises(HTTPException):
        run(auth.require_food_admin(None, f"Bearer {user}"))
    assert run(auth.require_food_admin(None, f"Bearer {admin}")) == "nutritionist"
