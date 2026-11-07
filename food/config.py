"""All tunable settings for the food engine, read from environment variables.

Every value has a safe default, so the module runs with only GEMINI_API_KEY set.
Inside host-backend these come from the same ECS task environment as the
other settings (see docs/08-backend-integration.md).
"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _str(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class FoodSettings:
    # Which AI reads the photo/text/voice: "gemini" or "bedrock" (Claude on AWS).
    ai_provider: str
    gemini_api_key: str
    gemini_model: str
    bedrock_model_id: str
    bedrock_region: str
    ai_timeout_seconds: float

    # "dev" = fake tokens for local testing, "host" = real host-app login tokens.
    auth_mode: str
    # "json" = files on disk (local testing), "mongo" = MongoDB.
    store: str
    json_store_dir: Path

    # True  = look each dish up in the food database (INDB), AI estimate as fallback.
    # False = use only the AI's own nutrient estimate; the database is never touched.
    use_database: bool

    # Privacy. False (default) = no learning data at all; a scan is kept only until the
    # user saves it (or pending_minutes); a saved meal keeps dish names + the six
    # DIARY_NUTRIENTS and is auto-deleted after history_days. True = the full
    # learning system (corrections, personal portions, review queue).
    # See docs/03-request-flow.md "Privacy mode".
    store_details: bool
    history_days: int
    pending_minutes: int

    # Name-matching thresholds (0..1). See docs/04-matching-and-nutrients.md.
    match_auto_threshold: float
    match_ask_threshold: float

    max_items: int
    max_image_bytes: int
    max_audio_bytes: int
    rate_limit_per_minute: int
    rate_limit_per_day: int

    # Learning from corrections. See docs/05-learning-and-fake-data.md.
    promote_min_users: int
    promote_min_agreement: float
    min_trust_for_global: float
    auto_promote: bool
    max_global_corrections_per_day: int


@lru_cache
def get_food_settings() -> FoodSettings:
    return FoodSettings(
        ai_provider=_str("FOOD_AI_PROVIDER", "gemini").lower(),
        gemini_api_key=_str("GEMINI_API_KEY", ""),
        gemini_model=_str("FOOD_GEMINI_MODEL", "gemini-3.5-flash-lite"),
        bedrock_model_id=_str("FOOD_BEDROCK_MODEL_ID", ""),
        bedrock_region=_str("FOOD_BEDROCK_REGION", "ap-south-1"),
        ai_timeout_seconds=_float("FOOD_AI_TIMEOUT_SECONDS", 60),
        auth_mode=_str("FOOD_AUTH_MODE", "host").lower(),
        store=_str("FOOD_STORE", "mongo").lower(),
        json_store_dir=Path(_str("FOOD_JSON_STORE_DIR", "local_store")),
        use_database=_bool("FOOD_USE_DATABASE", False),
        store_details=_bool("FOOD_STORE_DETAILS", False),
        history_days=_int("FOOD_HISTORY_DAYS", 30),
        pending_minutes=_int("FOOD_PENDING_MINUTES", 60),
        match_auto_threshold=_float("FOOD_MATCH_AUTO_THRESHOLD", 0.86),
        match_ask_threshold=_float("FOOD_MATCH_ASK_THRESHOLD", 0.50),
        max_items=_int("FOOD_MAX_ITEMS", 15),
        max_image_bytes=_int("FOOD_MAX_IMAGE_BYTES", 6 * 1024 * 1024),
        max_audio_bytes=_int("FOOD_MAX_AUDIO_BYTES", 10 * 1024 * 1024),
        rate_limit_per_minute=_int("FOOD_RATE_LIMIT_PER_MINUTE", 10),
        rate_limit_per_day=_int("FOOD_RATE_LIMIT_PER_DAY", 150),
        promote_min_users=_int("FOOD_PROMOTE_MIN_USERS", 3),
        promote_min_agreement=_float("FOOD_PROMOTE_MIN_AGREEMENT", 0.7),
        min_trust_for_global=_float("FOOD_MIN_TRUST_FOR_GLOBAL", 0.3),
        auto_promote=_bool("FOOD_AUTO_PROMOTE", False),
        max_global_corrections_per_day=_int("FOOD_MAX_GLOBAL_CORRECTIONS_PER_DAY", 30),
    )
