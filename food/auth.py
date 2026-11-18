"""Who is calling? Two modes, picked by FOOD_AUTH_MODE.

"jwt" (production): the client sends `Authorization: Bearer <token>`, an HS256 JWT
    signed with FOOD_AUTH_SECRET. The token's `sub` claim is the user's phone number
    and must match `phoneNumber` in the request body. Nutritionist/admin routes need
    `"role": "admin"` in the token. Create tokens with scripts/make_token.py or from
    your own login service using the same secret.

"dev" (local testing only): no real login. The token is "dev-<phone>"
    (e.g. "Bearer dev-9876543210") and the admin token is "dev-admin".
    NEVER deploy with FOOD_AUTH_MODE=dev.
"""
import base64
import hashlib
import hmac
import json
import re
import time

from fastapi import Header, HTTPException, Request, status

from .config import get_food_settings


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)[-10:]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(phone: str, secret: str, role: str = "user", ttl_seconds: int = 7 * 24 * 3600) -> str:
    """Create an HS256 JWT for a user (or an admin with role="admin")."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {"sub": _digits(phone) if role != "admin" else phone, "role": role, "iat": now, "exp": now + ttl_seconds}
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def decode_token(token: str, secret: str) -> dict:
    """Verify signature and expiry; return the claims or raise 401."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        if header.get("alg") != "HS256":
            raise ValueError("unsupported alg")
        expected = hmac.new(secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_b64)):
            raise ValueError("bad signature")
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token.") from exc
    if int(claims.get("exp", 0)) < time.time():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired.")
    return claims


def _jwt_claims(authorization: str) -> dict:
    secret = get_food_settings().auth_secret
    if not secret:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "FOOD_AUTH_SECRET is not set.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    return decode_token(token, secret)


async def verify_app_user(phone: str, authorization: str) -> str:
    """Return the caller's user id (normalised phone) or raise 401/403."""
    user = _digits(phone)
    if get_food_settings().auth_mode == "jwt":
        claims = _jwt_claims(authorization)
        if not user or _digits(str(claims.get("sub", ""))) != user:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Token does not match user phone.")
        return user

    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith("dev-"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    if not user or _digits(token) != user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token does not match user phone.")
    return user


async def require_food_admin(
    request: Request,
    authorization: str = Header(default="", alias="Authorization"),
) -> str:
    """FastAPI dependency for nutritionist/admin routes. Returns who decided."""
    if get_food_settings().auth_mode == "jwt":
        claims = _jwt_claims(authorization)
        if claims.get("role") != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role.")
        return str(claims.get("sub", "admin"))

    if authorization.removeprefix("Bearer ").strip() != "dev-admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role.")
    return "dev-admin"
