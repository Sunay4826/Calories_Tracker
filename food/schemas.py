"""Request bodies for every route (FastAPI validates them automatically).

Field names are camelCase like the rest of the host API.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MealType = Literal["breakfast", "lunch", "dinner", "snack"]


class AnalyzeRequest(BaseModel):
    phoneNumber: str = Field(min_length=6, max_length=20)
    text: str | None = Field(default=None, max_length=1000)
    imageBase64: str | None = None          # JPEG/PNG/WebP/HEIC, max 6 MB after decoding
    imageMimeType: str | None = None
    audioBase64: str | None = None          # voice note (Gemini only), max 10 MB
    audioMimeType: str | None = None
    mealType: MealType | None = None


class FixRequest(BaseModel):
    phoneNumber: str = Field(min_length=6, max_length=20)
    analysisId: str
    action: Literal["replace", "set_amount", "remove", "add", "use_estimate"]
    itemId: str | None = None               # required except for "add"
    foodCode: str | None = None             # replace/add: food picked from search or alternatives
    correctedName: str | None = Field(default=None, max_length=120)  # replace/add: typed name
    grams: float | None = Field(default=None, gt=0, le=3000)
    quantity: float | None = Field(default=None, gt=0, le=100)
    unit: str | None = Field(default=None, max_length=30)


class ConfirmRequest(BaseModel):
    phoneNumber: str = Field(min_length=6, max_length=20)
    analysisId: str
    mealType: MealType | None = None
    loggedAt: datetime | None = None        # default: now


class SearchRequest(BaseModel):
    phoneNumber: str = Field(min_length=6, max_length=20)
    query: str = Field(min_length=2, max_length=80)
    limit: int = Field(default=10, ge=1, le=25)


class HistoryRequest(BaseModel):
    phoneNumber: str = Field(min_length=6, max_length=20)
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # last day (IST), default today
    days: int = Field(default=1, ge=1, le=31)


class DeleteMealRequest(BaseModel):
    phoneNumber: str = Field(min_length=6, max_length=20)
    mealId: str


class ReviewDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=300)
