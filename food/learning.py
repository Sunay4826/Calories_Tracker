"""Learning from user corrections without letting fake corrections poison everyone.

Three levels of trust (full explanation: docs/05-learning-and-fake-data.md):

1. PERSONAL - applies at once, only to the user who made it.
   "When I say 'dal', I mean Moong dal"  -> personal alias
   "My roti weighs 30 g"                 -> personal portion

2. CANDIDATE - the same correction from several different trusted users.
   When >= FOOD_PROMOTE_MIN_USERS users map an AI name to the same food, and
   they are >= FOOD_PROMOTE_MIN_AGREEMENT of everyone who corrected that name,
   it goes into the nutritionist review queue.

3. GLOBAL - only after a nutritionist approves (or FOOD_AUTO_PROMOTE=true).
   Becomes an alias used for every user.

Trust score per user starts at 0.5. Approved suggestions +0.1, rejected -0.2.
Below FOOD_MIN_TRUST_FOR_GLOBAL a user's corrections stay personal only.
Each user can send at most FOOD_MAX_GLOBAL_CORRECTIONS_PER_DAY to the shared pool.
"""
import time
import uuid
from datetime import datetime, timezone

from .config import FoodSettings
from .food_db import FoodDb
from .matcher import normalize
from .store import ALIASES, CORRECTIONS, OVERRIDES, PROFILES, REVIEW, Store

START_TRUST = 0.5
APPROVED_BONUS = 0.1
REJECTED_PENALTY = 0.2
CACHE_SECONDS = 60


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Learning:
    def __init__(self, store: Store, db: FoodDb, settings: FoodSettings):
        self.store, self.db, self.settings = store, db, settings
        self._cache: dict[str, tuple[float, dict]] = {}

    # ---------- per-user profile ----------

    async def get_profile(self, user_id: str) -> dict:
        profile = await self.store.get(PROFILES, user_id)
        return profile or {
            "id": user_id, "trust": START_TRUST, "aliases": {}, "portions": {},
            "stats": {"corrections": 0, "approved": 0, "rejected": 0},
            "globalToday": {"date": "", "count": 0}, "createdAt": now_iso(),
        }

    async def _save_profile(self, profile: dict) -> None:
        await self.store.put(PROFILES, profile)

    # ---------- shared data (cached for a minute) ----------

    async def _cached(self, name: str, loader) -> dict:
        hit = self._cache.get(name)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = await loader()
        self._cache[name] = (time.time(), value)
        return value

    async def global_aliases(self) -> dict[str, str]:
        async def load():
            return {d["aiKey"]: d["foodCode"] for d in await self.store.find(ALIASES, limit=100000)}
        return await self._cached("aliases", load)

    async def overrides(self) -> dict[str, str]:
        """Food codes a nutritionist marked as unreliable -> note."""
        async def load():
            return {d["id"]: d.get("note", "") for d in await self.store.find(OVERRIDES, limit=100000)}
        return await self._cached("overrides", load)

    # ---------- recording corrections ----------

    async def record_identity(
        self, user_id: str, *, ai_name: str, food_code: str | None, corrected_text: str,
        original_code: str | None, analysis_id: str, kind: str = "identity",
    ) -> None:
        """User said "this item is actually <food_code / corrected_text>"."""
        key = normalize(ai_name)
        profile = await self.get_profile(user_id)
        profile["stats"]["corrections"] += 1
        if food_code and key:
            profile["aliases"][key] = food_code

        today = now_iso()[:10]
        if profile["globalToday"]["date"] != today:
            profile["globalToday"] = {"date": today, "count": 0}
        shareable = bool(
            food_code and key and kind == "identity"
            and food_code != original_code
            and profile["trust"] >= self.settings.min_trust_for_global
            and profile["globalToday"]["count"] < self.settings.max_global_corrections_per_day
        )
        if shareable:
            profile["globalToday"]["count"] += 1

        await self.store.put(CORRECTIONS, {
            "id": uuid.uuid4().hex, "userId": user_id, "kind": kind, "aiName": ai_name, "aiKey": key,
            "originalCode": original_code, "foodCode": food_code, "correctedText": corrected_text[:200],
            "analysisId": analysis_id, "status": "candidate" if shareable else "personal",
            "createdAt": now_iso(),
        })
        await self._save_profile(profile)
        if shareable:
            await self._check_consensus(key, ai_name)

    async def record_portion(
        self, user_id: str, *, food_key: str, unit: str, grams_per_unit: float | None,
        before_grams: float, after_grams: float, analysis_id: str,
    ) -> None:
        """User changed the amount. Remember their usual size per unit (personal only)."""
        profile = await self.get_profile(user_id)
        profile["stats"]["corrections"] += 1
        if grams_per_unit:
            profile["portions"].setdefault(food_key, {})[unit] = round(grams_per_unit, 1)
        await self.store.put(CORRECTIONS, {
            "id": uuid.uuid4().hex, "userId": user_id, "kind": "portion", "foodKey": food_key,
            "unit": unit, "gramsPerUnit": grams_per_unit, "beforeGrams": before_grams,
            "afterGrams": after_grams, "analysisId": analysis_id, "status": "personal",
            "createdAt": now_iso(),
        })
        await self._save_profile(profile)

    async def record_removal(self, user_id: str, *, ai_name: str, analysis_id: str) -> None:
        await self.store.put(CORRECTIONS, {
            "id": uuid.uuid4().hex, "userId": user_id, "kind": "remove", "aiName": ai_name,
            "analysisId": analysis_id, "status": "personal", "createdAt": now_iso(),
        })

    async def record_data_conflict(self, *, food_code: str, db_kcal_100g: float, ai_kcal_100g: float) -> None:
        """Database and AI disagree a lot on calories -> ask a nutritionist to check the row."""
        review_id = f"data:{food_code}"
        item = await self.store.get(REVIEW, review_id) or {
            "id": review_id, "type": "data_conflict", "foodCode": food_code,
            "foodName": self.db.by_code[food_code]["name"], "dbKcalPer100g": round(db_kcal_100g, 1),
            "aiKcalSamples": [], "count": 0, "status": "pending", "createdAt": now_iso(),
        }
        item["count"] += 1
        item["aiKcalSamples"] = (item["aiKcalSamples"] + [round(ai_kcal_100g, 1)])[-10:]
        item["updatedAt"] = now_iso()
        await self.store.put(REVIEW, item)

    # ---------- consensus -> review queue ----------

    async def _check_consensus(self, key: str, ai_name: str) -> None:
        corrections = await self.store.find(CORRECTIONS, {"aiKey": key, "kind": "identity"}, limit=5000)
        latest: dict[str, str] = {}  # user -> food code (newest correction wins)
        for c in sorted(corrections, key=lambda c: c["createdAt"]):
            if c["status"] in ("candidate", "approved") and c.get("foodCode"):
                latest[c["userId"]] = c["foodCode"]

        trusted: dict[str, str] = {}
        for user, code in latest.items():
            profile = await self.get_profile(user)
            if profile["trust"] >= self.settings.min_trust_for_global:
                trusted[user] = code
        if not trusted:
            return

        votes: dict[str, list[str]] = {}
        for user, code in trusted.items():
            votes.setdefault(code, []).append(user)
        code, supporters = max(votes.items(), key=lambda kv: len(kv[1]))
        agreement = len(supporters) / len(trusted)
        if len(supporters) < self.settings.promote_min_users or agreement < self.settings.promote_min_agreement:
            return

        review_id = f"alias:{key}"
        existing = await self.store.get(REVIEW, review_id)
        if existing and existing["status"] != "pending" and existing["foodCode"] == code:
            return  # already decided for this exact suggestion
        await self.store.put(REVIEW, {
            "id": review_id, "type": "alias", "aiKey": key, "aiName": ai_name, "foodCode": code,
            "foodName": self.db.by_code[code]["name"], "supporters": supporters,
            "usersWhoCorrected": len(trusted), "agreement": round(agreement, 2),
            "status": "pending", "createdAt": (existing or {}).get("createdAt", now_iso()), "updatedAt": now_iso(),
        })
        if self.settings.auto_promote:
            await self.decide(review_id, "approve", "auto")

    # ---------- nutritionist decisions ----------

    async def review_queue(self, status: str = "pending") -> list[dict]:
        return await self.store.find(REVIEW, {"status": status}, limit=500)

    async def decide(self, review_id: str, decision: str, reviewer: str, note: str = "") -> dict | None:
        item = await self.store.get(REVIEW, review_id)
        if not item:
            return None
        item.update(status="approved" if decision == "approve" else "rejected",
                    decidedBy=reviewer, decidedAt=now_iso(), note=note[:300])
        await self.store.put(REVIEW, item)

        if item["type"] == "alias":
            if decision == "approve":
                await self.store.put(ALIASES, {
                    "id": item["aiKey"], "aiKey": item["aiKey"], "foodCode": item["foodCode"],
                    "approvedBy": reviewer, "createdAt": now_iso(),
                })
            for user in item["supporters"]:
                profile = await self.get_profile(user)
                if decision == "approve":
                    profile["trust"] = min(1.0, round(profile["trust"] + APPROVED_BONUS, 2))
                    profile["stats"]["approved"] += 1
                else:
                    profile["trust"] = max(0.0, round(profile["trust"] - REJECTED_PENALTY, 2))
                    profile["stats"]["rejected"] += 1
                await self._save_profile(profile)
            for c in await self.store.find(CORRECTIONS, {"aiKey": item["aiKey"], "kind": "identity"}, limit=5000):
                if c.get("foodCode") == item["foodCode"] and c["status"] == "candidate":
                    c["status"] = item["status"]
                    await self.store.put(CORRECTIONS, c)
        elif item["type"] == "data_conflict" and decision == "approve":
            await self.store.put(OVERRIDES, {
                "id": item["foodCode"], "quality": "suspect",
                "note": note or "Marked unreliable by nutritionist", "createdAt": now_iso(),
            })

        self._cache.clear()
        return item
