"""Who is calling? Two modes, picked by FOOD_AUTH_MODE.

"host" (production): the app sends the normal login token
    (Authorization: Bearer <token>) plus phoneNumber in the body, exactly like
    every other /api/auth/... route. We reuse host-backend's own checks:
    require_app_user_phone_match (token must belong to that phone) and
    require_admin_access for the nutritionist review routes.

"dev" (local testing only): no real login. The token is "dev-<phone>"
    (e.g. "Bearer dev-9876543210") and the admin token is "dev-admin".
    NEVER deploy with FOOD_AUTH_MODE=dev.
"""
import re

from fastapi import Header, HTTPException, Request, status

from .config import get_food_settings


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)[-10:]


async def verify_app_user(phone: str, authorization: str) -> str:
    """Return the caller's user id (normalised phone) or raise 401/403."""
    if get_food_settings().auth_mode == "host":
        from app.core.security import normalize_phone_identity, require_app_user_phone_match

        require_app_user_phone_match(authorization, phone)
        return normalize_phone_identity(phone)

    token = authorization.removeprefix("Bearer ").strip()
    if not token.startswith("dev-"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    user = _digits(phone)
    if not user or _digits(token) != user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token does not match user phone.")
    return user


async def require_food_admin(
    request: Request,
    authorization: str = Header(default="", alias="Authorization"),
) -> str:
    """FastAPI dependency for nutritionist/admin routes. Returns who decided."""
    if get_food_settings().auth_mode == "host":
        from app.core.security import extract_admin_actor_from_bearer, require_admin_access

        await require_admin_access(request, authorization)
        return extract_admin_actor_from_bearer(authorization)

    if authorization.removeprefix("Bearer ").strip() != "dev-admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role.")
    return "dev-admin"
