"""HTTP routes. Inside host-backend, add to app/gateway.py:

    from app.services.food.api import router as food_router, admin_router as food_admin_router
    app.include_router(food_router)
    app.include_router(food_admin_router)

App routes   (login token + phoneNumber, like other /api/auth/ routes):
    POST /api/auth/food/analyze       photo / text / voice -> items + nutrients
    POST /api/auth/food/fix           change one item (replace / amount / remove / add / use_estimate)
    POST /api/auth/food/confirm       save the meal to the diary
    POST /api/auth/food/search        search the food database by name
    POST /api/auth/food/history       meals + daily totals
    POST /api/auth/food/meals/delete  delete a saved meal
Admin routes (nutritionists, admin token):
    GET  /api/admin/food/review-queue?status=pending
    POST /api/admin/food/review/{reviewId}   approve / reject
Full reference with examples: docs/06-api-reference.md
"""
from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from .ai_client import get_ai_client
from .auth import require_food_admin, verify_app_user
from .config import get_food_settings
from .engine import FoodEngine
from .food_db import get_food_db, load_seed_aliases
from .learning import Learning
from .matcher import Matcher
from .ratelimit import limiter
from .schemas import (
    AnalyzeRequest, ConfirmRequest, DeleteMealRequest, FixRequest, HistoryRequest,
    ReviewDecisionRequest, SearchRequest,
)
from .store import get_store

router = APIRouter(prefix="/api/auth/food", tags=["app-food-log"])
admin_router = APIRouter(prefix="/api/admin/food", tags=["admin-food-log"])


@lru_cache
def get_engine() -> FoodEngine:
    """Built once per process: loads the 1,014-food database and name index."""
    settings, db, store = get_food_settings(), get_food_db(), get_store()
    return FoodEngine(
        db=db, matcher=Matcher(db, load_seed_aliases()), ai=get_ai_client(), store=store,
        learning=Learning(store, db, settings), settings=settings,
    )


def _limit(user_id: str) -> None:
    s = get_food_settings()
    limiter.check(f"food:{user_id}", s.rate_limit_per_minute, s.rate_limit_per_day)


@router.post("/analyze")
async def analyze(body: AnalyzeRequest, authorization: str = Header(default="", alias="Authorization"),
                  engine: FoodEngine = Depends(get_engine)) -> dict:
    user_id = await verify_app_user(body.phoneNumber, authorization)
    _limit(user_id)
    return await engine.analyze(
        user_id, text=body.text, image_b64=body.imageBase64, image_mime=body.imageMimeType,
        audio_b64=body.audioBase64, audio_mime=body.audioMimeType, meal_type=body.mealType,
    )


@router.post("/fix")
async def fix(body: FixRequest, authorization: str = Header(default="", alias="Authorization"),
              engine: FoodEngine = Depends(get_engine)) -> dict:
    user_id = await verify_app_user(body.phoneNumber, authorization)
    if body.action in ("replace", "add") and not body.foodCode:
        _limit(user_id)  # typed names may need an AI call
    return await engine.fix(
        user_id, analysis_id=body.analysisId, action=body.action, item_id=body.itemId,
        food_code=body.foodCode, corrected_name=body.correctedName, grams=body.grams,
        quantity=body.quantity, unit=body.unit,
    )


@router.post("/confirm")
async def confirm(body: ConfirmRequest, authorization: str = Header(default="", alias="Authorization"),
                  engine: FoodEngine = Depends(get_engine)) -> dict:
    user_id = await verify_app_user(body.phoneNumber, authorization)
    meal = await engine.confirm(user_id, analysis_id=body.analysisId, meal_type=body.mealType, logged_at=body.loggedAt)
    return {"meal": meal}


@router.post("/search")
async def search(body: SearchRequest, authorization: str = Header(default="", alias="Authorization"),
                 engine: FoodEngine = Depends(get_engine)) -> dict:
    await verify_app_user(body.phoneNumber, authorization)
    return {"results": engine.search(body.query, body.limit)}


@router.post("/history")
async def history(body: HistoryRequest, authorization: str = Header(default="", alias="Authorization"),
                  engine: FoodEngine = Depends(get_engine)) -> dict:
    user_id = await verify_app_user(body.phoneNumber, authorization)
    return await engine.history(user_id, date=body.date, days=body.days)


@router.post("/meals/delete")
async def delete_meal(body: DeleteMealRequest, authorization: str = Header(default="", alias="Authorization"),
                      engine: FoodEngine = Depends(get_engine)) -> dict:
    user_id = await verify_app_user(body.phoneNumber, authorization)
    await engine.delete_meal(user_id, body.mealId)
    return {"deleted": True}


@admin_router.get("/review-queue")
async def review_queue(state: str = Query("pending", alias="status"), _admin: str = Depends(require_food_admin),
                       engine: FoodEngine = Depends(get_engine)) -> dict:
    return {"items": await engine.learning.review_queue(state)}


@admin_router.post("/review/{review_id}")
async def review_decision(review_id: str, body: ReviewDecisionRequest, admin: str = Depends(require_food_admin),
                          engine: FoodEngine = Depends(get_engine)) -> dict:
    item = await engine.learning.decide(review_id, body.decision, admin, body.note)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review item not found.")
    return {"item": item}
