"""The food engine: the whole flow from "user sent a photo" to "meal saved".

    analyze()  photo/text/voice -> AI -> match each dish in INDB -> nutrients
               -> saved as a PENDING analysis the user can review
    fix()      user says an item is wrong -> replace / change amount / remove / add /
               use the AI estimate instead of the database value
               -> nutrients recalculated, correction recorded for learning
    confirm()  user taps "Looks right" -> analysis becomes a meal in the diary

Privacy mode (FOOD_STORE_DETAILS=false, the default): nothing is learned or kept
except the diary. A pending analysis lives at most FOOD_PENDING_MINUTES and is deleted
on confirm; a meal keeps dish names + DIARY_NUTRIENTS and expires after FOOD_HISTORY_DAYS.
    search()   type-ahead search of the food database for the "pick it yourself" screen
    history()  the user's meals and daily totals

Step-by-step walkthrough with examples: docs/03-request-flow.md.
"""
import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from . import nutrients as nx
from .ai_client import AiClient, AiError
from .config import FoodSettings
from .food_db import FoodDb
from .learning import Learning, now_iso
from .matcher import Matcher, normalize
from .parsing import AiItem, parse_recognition
from .prompts import choose_prompt, correction_prompt, recognition_prompt
from .store import ALL_COLLECTIONS, ANALYSES, MEALS, PROFILES, Store

IST = timezone(timedelta(hours=5, minutes=30))
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
AUDIO_TYPES = {
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/aac", "audio/ogg",
    "audio/flac", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/webm",
}
DISAGREE_RATIO = 2.5      # DB vs AI calories differ more than this -> needs review
LOW_CONFIDENCE = 0.6      # AI less sure than this about the dish -> needs review
PORTION_SANITY = (0.2, 5.0)  # personal portion kept only within 0.2x-5x of the AI's guess

# Why an item is flagged; the app shows the matching message.
REASONS = {
    "not_in_database": "Not in our food database yet: nutrients are an AI estimate.",
    "database_value_unreliable": "Database value looked wrong for this dish: using an AI estimate.",
    "ai_db_disagree": "AI and database disagree a lot on calories: please check.",
    "low_confidence": "Not sure about this dish: please check.",
    "no_nutrient_data": "No nutrient data available for this item.",
    "default_amount": "Amount was guessed: please check.",
}


def _decode(data: str, mime: str, allowed: set[str], max_bytes: int, label: str) -> bytes:
    if mime not in allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported {label} type: {mime}")
    if len(data) > max_bytes * 4 // 3 + 4:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{label.capitalize()} is too large.")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid {label} data.") from exc
    if len(raw) > max_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{label.capitalize()} is too large.")
    return raw


class FoodEngine:
    def __init__(self, *, db: FoodDb, matcher: Matcher, ai: AiClient, store: Store,
                 learning: Learning, settings: FoodSettings):
        self.db, self.matcher, self.ai, self.store = db, matcher, ai, store
        self.learning, self.settings = learning, settings

    # ======================= analyze =======================

    async def analyze(self, user_id: str, *, text: str | None, image_b64: str | None, image_mime: str | None,
                      audio_b64: str | None, audio_mime: str | None, meal_type: str | None) -> dict:
        text = " ".join((text or "").split())[:1000] or None
        if not (text or image_b64 or audio_b64):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send a photo, a description or a voice note.")

        media, input_kind, image_hash = [], [], None
        if image_b64:
            mime = (image_mime or "image/jpeg").lower()
            raw = _decode(image_b64, mime, IMAGE_TYPES, self.settings.max_image_bytes, "image")
            image_hash = hashlib.sha256(raw).hexdigest()  # we keep only the hash, never the photo
            media.append((mime, image_b64))
            input_kind.append("photo")
        if audio_b64:
            mime = (audio_mime or "audio/aac").lower()
            _decode(audio_b64, mime, AUDIO_TYPES, self.settings.max_audio_bytes, "audio")
            media.append((mime, audio_b64))
            input_kind.append("voice")
        if text:
            input_kind.append("text")

        # Step 1: AI recognises dishes + amounts (+ its own nutrient estimate).
        prompt = recognition_prompt(has_image=bool(image_b64), has_audio=bool(audio_b64), user_text=text)
        try:
            recognition = parse_recognition(await self.ai.generate_json(prompt, media), self.settings.max_items)
        except AiError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Food recognition failed: {exc}") from exc

        if recognition.not_food or not recognition.items:
            return {"analysisId": None, "notFood": True, "items": [], "totals": {},
                    "transcript": recognition.transcript, "warnings": recognition.warnings,
                    "message": "No food found. Try a clearer photo or describe the meal."}

        profile = await self._profile(user_id)
        if self.settings.use_database:
            matches = await self._match_all(recognition, input_kind, profile)
        else:
            # FOOD_USE_DATABASE=false: skip steps 2-3, every item uses the AI estimate.
            matches = [_no_match() for _ in recognition.items]

        # Step 4: nutrients for every item (database x grams, or AI estimate).
        overrides = await self.learning.overrides() if self.settings.use_database else {}
        items = []
        for ai_item, match in zip(recognition.items, matches):
            items.append(await self._build_item(ai_item, match, profile, overrides))

        analysis = {
            "id": uuid.uuid4().hex, "userId": user_id, "status": "pending", "createdAt": now_iso(),
            "inputKind": input_kind, "userText": text, "transcript": recognition.transcript,
            "imageSha256": image_hash, "mealType": meal_type, "items": items,
            "warnings": recognition.warnings,
        }
        if not self.settings.store_details:
            # Kept only so the user can fix items before saving; no photo hash, text or transcript.
            stored = {k: v for k, v in analysis.items() if k not in ("userText", "transcript", "imageSha256")}
            stored["expireAt"] = (datetime.now(timezone.utc) + timedelta(minutes=self.settings.pending_minutes)).isoformat()
            await self.store.put(ANALYSES, stored)
        else:
            await self.store.put(ANALYSES, analysis)
        return self.public(analysis)

    async def _profile(self, user_id: str) -> dict:
        """Personal aliases and usual portions; empty in privacy mode (nothing is learned)."""
        if self.settings.store_details:
            return await self.learning.get_profile(user_id)
        return {"aliases": {}, "portions": {}}

    async def _match_all(self, recognition, input_kind: list[str], profile: dict) -> list[dict]:
        # Step 2: find each dish in the database.
        # Personal name fixes ("when the AI says paratha it's really my thepla") are
        # used only for photo-only scans, where the AI guessed the name. If the user
        # typed or said the dish, we believe their words.
        global_aliases = await self.learning.global_aliases()
        personal = profile["aliases"] if input_kind == ["photo"] else None
        matches = [self.matcher.match([i.name, *i.search_terms], personal, global_aliases)
                   for i in recognition.items]

        # Step 3: unsure matches -> one extra AI call to pick the right candidate (or none).
        # Sure = the user's own alias, or the dish's MAIN name matched an alias or
        # scored >= the auto threshold. A match found only through one of the AI's
        # "other names" (e.g. biryani -> "chicken pulao") is always double-checked.
        def is_sure(m: dict) -> bool:
            return m["method"] == "personal_alias" or (
                m["viaMainName"] and (m["method"] == "alias" or m["score"] >= self.settings.match_auto_threshold))

        unsure = [n for n, m in enumerate(matches) if not is_sure(m) and m["score"] >= self.settings.match_ask_threshold]
        if unsure:
            await self._ai_choose(recognition.items, matches, unsure, recognition.warnings)
        for m in matches:
            if not is_sure(m) and m["method"] != "ai_choice":
                m["foodCode"] = None  # still unsure -> treat as "not in database"
        return matches

    async def _ai_choose(self, ai_items: list[AiItem], matches: list[dict], unsure: list[int], warnings: list[str]) -> None:
        entries = [{
            "index": n,
            "dish": ai_items[n].name,
            "description": ", ".join([*ai_items[n].search_terms, ai_items[n].cooking_style]).strip(", "),
            "candidates": [{"code": c["foodCode"], "name": c["foodName"]} for c in matches[n]["candidates"][:6]],
        } for n in unsure]
        try:
            reply = await self.ai.generate_json(choose_prompt(entries))
        except AiError:
            warnings.append("Could not double-check some database matches.")
            return
        for choice in reply.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            n, code = choice.get("index"), choice.get("code")
            if n in unsure and code and any(c["foodCode"] == code for c in matches[n]["candidates"]):
                matches[n].update(foodCode=code, method="ai_choice", score=next(
                    c["score"] for c in matches[n]["candidates"] if c["foodCode"] == code))

    async def _build_item(self, ai_item: AiItem, match: dict, profile: dict, overrides: dict,
                          item_id: str | None = None, grams_override: float | None = None) -> dict:
        food = self.db.get(match.get("foodCode"))
        reasons: list[str] = []

        # How much? user > personal usual portion > AI estimate > database serving.
        quantity, unit = ai_item.quantity, ai_item.unit
        food_key = food["code"] if food else normalize(ai_item.name)
        usual = profile["portions"].get(food_key, {}).get(unit)
        if grams_override:
            grams, grams_source = grams_override, "user"
        elif usual:
            grams, grams_source = round(usual * quantity, 1), "your_usual"
        elif ai_item.grams:
            grams, grams_source = ai_item.grams, "ai"
        elif food and food["serving"]:
            grams, grams_source = round(food["serving"]["grams"] * quantity, 1), "database_serving"
            reasons.append("default_amount")
        else:
            grams, grams_source = 100.0, "default"
            reasons.append("default_amount")

        # AI estimate turned into per-100 g so it can be rescaled when grams change.
        estimate_100g = nx.to_per100g(ai_item.estimate, ai_item.grams) if ai_item.estimate and ai_item.grams else None
        quality = "suspect" if food and (food["code"] in overrides or food["quality"] == "suspect") else "ok"

        if food and quality == "ok":
            source, per100 = "database", food["per100g"]
            db_kcal, ai_kcal = per100.get("energy_kcal"), (estimate_100g or {}).get("energy_kcal")
            if db_kcal and ai_kcal and not (1 / DISAGREE_RATIO <= ai_kcal / db_kcal <= DISAGREE_RATIO):
                reasons.append("ai_db_disagree")
                await self.learning.record_data_conflict(food_code=food["code"], db_kcal_100g=db_kcal, ai_kcal_100g=ai_kcal)
        elif estimate_100g:
            source, per100 = "ai_estimate", estimate_100g
            if food:
                reasons.append("database_value_unreliable")
            elif self.settings.use_database:
                reasons.append("not_in_database")  # AI-only mode: the estimate is expected, not a warning
        elif food:
            source, per100 = "database", food["per100g"]
            reasons.append("database_value_unreliable")
        else:
            source, per100 = "none", {}
            reasons.append("no_nutrient_data")
        if ai_item.confidence < LOW_CONFIDENCE:
            reasons.append("low_confidence")

        # Units the user can switch between on the edit screen.
        unit_options = []
        if unit != "g" and ai_item.grams and quantity:
            unit_options.append({"unit": unit, "gramsPerUnit": round(ai_item.grams / quantity, 1)})
        for personal_unit, gpu in profile["portions"].get(food_key, {}).items():
            unit_options.append({"unit": personal_unit, "gramsPerUnit": gpu, "yours": True})
        if food and food["serving"]:
            unit_options.append({"unit": food["serving"]["unit"], "gramsPerUnit": food["serving"]["grams"]})
        unit_options.append({"unit": "g", "gramsPerUnit": 1})
        seen, unique_units = set(), []
        for option in unit_options:
            if option["unit"] not in seen:
                seen.add(option["unit"])
                unique_units.append(option)

        # "Not this? Maybe: ..." suggestions.
        chosen = food["code"] if food else None
        alternatives = [{"foodCode": c["foodCode"], "foodName": c["foodName"]}
                        for c in match.get("candidates", []) if c["foodCode"] != chosen][:5]

        return {
            "itemId": item_id or uuid.uuid4().hex[:12],
            "name": food["name"] if food else ai_item.name,
            "aiName": ai_item.name,
            "searchTerms": ai_item.search_terms,
            "quantity": quantity,
            "unit": unit,
            "grams": round(grams, 1),
            "gramsSource": grams_source,
            "source": source,
            "match": ({"foodCode": food["code"], "foodName": food["name"], "score": match["score"],
                       "method": match["method"], "dataSource": food["source"]} if food else None),
            "dataQuality": quality if food else None,
            "per100g": per100,
            "aiEstimatePer100g": estimate_100g,
            "nutrients": nx.scale(per100, grams),
            "confidence": ai_item.confidence,
            "needsReview": bool(reasons),
            "reviewReasons": reasons,
            "reviewMessages": [REASONS[r] for r in reasons],
            "alternatives": alternatives,
            "aiAlternatives": ai_item.alternatives,
            "note": "Based on your earlier correction." if match.get("method") == "personal_alias" else "",
            "unitOptions": unique_units,
            "edited": False,
        }

    # ======================= fix =======================

    async def fix(self, user_id: str, *, analysis_id: str, action: str, item_id: str | None,
                  food_code: str | None, corrected_name: str | None, grams: float | None,
                  quantity: float | None, unit: str | None) -> dict:
        analysis = await self._own_pending(user_id, analysis_id)
        items = analysis["items"]
        index = next((n for n, i in enumerate(items) if i["itemId"] == item_id), None)
        if action != "add" and index is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found.")

        if action == "remove":
            removed = items.pop(index)
            if self.settings.store_details:
                await self.learning.record_removal(user_id, ai_name=removed["aiName"], analysis_id=analysis_id)

        elif action == "set_amount":
            items[index] = await self._set_amount(user_id, analysis_id, items[index], grams, quantity, unit)

        elif action == "use_estimate":
            # "The database number looks wrong, use the AI's estimate instead."
            item = items[index]
            if not item.get("aiEstimatePer100g"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "No AI estimate available for this item.")
            item.update(per100g=item["aiEstimatePer100g"], source="ai_estimate", edited=True,
                        nutrients=nx.scale(item["aiEstimatePer100g"], item["grams"]))
            item["reviewReasons"] = [r for r in item["reviewReasons"] if r != "ai_db_disagree"]
            item["reviewMessages"] = [REASONS[r] for r in item["reviewReasons"]]
            item["needsReview"] = bool(item["reviewReasons"])

        elif action in ("replace", "add"):
            if not food_code and not (corrected_name or "").strip():
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a food or type its name.")
            if food_code and not self.settings.use_database:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "The food database is turned off: type the dish name instead.")
            old = items[index] if action == "replace" else None
            new = await self._resolve_correction(
                user_id, old=old, food_code=food_code, corrected_name=(corrected_name or "").strip()[:120],
                grams=grams, quantity=quantity, unit=unit,
            )
            new["edited"] = True
            # Keep the AI's first (wrong) guess: the most useful training signal.
            new["originalAiName"] = (old.get("originalAiName") or old["aiName"]) if old else None
            if self.settings.store_details:
                await self.learning.record_identity(
                    user_id,
                    ai_name=old["aiName"] if old else new["aiName"],
                    food_code=new["match"]["foodCode"] if new["match"] else None,
                    corrected_text=corrected_name or food_code or "",
                    original_code=old["match"]["foodCode"] if old and old["match"] else None,
                    analysis_id=analysis_id,
                    kind="identity" if old else "missed_item",
                )
            if old:
                items[index] = new
            else:
                items.append(new)
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown action.")

        analysis["updatedAt"] = now_iso()
        await self.store.put(ANALYSES, analysis)
        return self.public(analysis)

    async def _set_amount(self, user_id, analysis_id, item, grams, quantity, unit) -> dict:
        option = next((o for o in item["unitOptions"] if o["unit"] == unit), None) if unit else None
        if grams is None and quantity and option:
            grams = quantity * option["gramsPerUnit"]
        if grams is None or not (1 <= grams <= 3000):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Amount must be between 1 g and 3000 g.")
        before = item["grams"]
        item.update(grams=round(grams, 1), gramsSource="user", edited=True,
                    nutrients=nx.scale(item["per100g"], grams))
        if quantity and unit:
            item.update(quantity=quantity, unit=unit)
        item["reviewReasons"] = [r for r in item["reviewReasons"] if r != "default_amount"]
        item["reviewMessages"] = [REASONS[r] for r in item["reviewReasons"]]
        item["needsReview"] = bool(item["reviewReasons"])

        if not self.settings.store_details:
            return item  # privacy mode: nothing is remembered

        # Remember "my katori is 200 g" for next time, only if it is believable.
        gpu = None
        if quantity and unit and unit != "g":
            gpu = grams / quantity
            ai_gpu = next((o["gramsPerUnit"] for o in item["unitOptions"] if o["unit"] == unit and not o.get("yours")), None)
            if ai_gpu and not (PORTION_SANITY[0] <= gpu / ai_gpu <= PORTION_SANITY[1]):
                gpu = None  # unusual -> keep for this meal only
        food_key = item["match"]["foodCode"] if item["match"] else normalize(item["aiName"])
        await self.learning.record_portion(
            user_id, food_key=food_key, unit=unit or "g", grams_per_unit=gpu,
            before_grams=before, after_grams=item["grams"], analysis_id=analysis_id,
        )
        return item

    async def _resolve_correction(self, user_id, *, old, food_code, corrected_name, grams, quantity, unit) -> dict:
        """Turn the user's correction into a new item ("auto-fix")."""
        profile = await self._profile(user_id)
        use_db = self.settings.use_database
        overrides = await self.learning.overrides() if use_db else {}
        keep_grams = grams or (old["grams"] if old else None)

        # a) User picked a database food directly -> exact.
        if food_code:
            food = self.db.get(food_code)
            if not food:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown food code.")
            base = AiItem(name=old["aiName"] if old else food["name"], search_terms=[], quantity=quantity or (old["quantity"] if old else 1),
                          unit=unit or (old["unit"] if old else "serving"), grams=keep_grams, cooking_style="",
                          confidence=1.0, alternatives=[], estimate=None)
            match = {"foodCode": food_code, "score": 1.0, "method": "user_choice",
                     "candidates": self.matcher.candidates([food["name"]])}
            item = await self._build_item(base, match, profile, overrides,
                                          item_id=old["itemId"] if old else None, grams_override=keep_grams)
            return item

        # b) User typed a name that clearly matches the database -> use it.
        match = self.matcher.match([corrected_name], profile["aliases"], await self.learning.global_aliases()) if use_db else None
        if match and (match["method"] != "fuzzy" or match["score"] >= self.settings.match_auto_threshold):
            base = AiItem(name=corrected_name, search_terms=[], quantity=quantity or (old["quantity"] if old else 1),
                          unit=unit or (old["unit"] if old else "serving"), grams=keep_grams, cooking_style="",
                          confidence=1.0, alternatives=[], estimate=None)
            if keep_grams:
                return await self._build_item(base, match, profile, overrides,
                                              item_id=old["itemId"] if old else None, grams_override=keep_grams)

        # c) Otherwise ask the AI about the user's dish, then match that.
        prompt = correction_prompt(original_name=old["aiName"] if old else "(an item the AI missed)",
                                   quantity=quantity or (old["quantity"] if old else 1),
                                   unit=unit or (old["unit"] if old else "serving"),
                                   grams=keep_grams or 100, corrected=corrected_name)
        try:
            recognition = parse_recognition(await self.ai.generate_json(prompt), 1)
        except AiError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Could not process the correction: {exc}") from exc
        if not recognition.items:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That doesn't look like a food. Please rephrase.")
        ai_item = recognition.items[0]
        if not use_db:
            match = _no_match()
        else:
            match = self.matcher.match([corrected_name, ai_item.name, *ai_item.search_terms],
                                       profile["aliases"], await self.learning.global_aliases())
            if match["method"] == "fuzzy" and match["score"] < self.settings.match_auto_threshold:
                match["foodCode"] = None
        return await self._build_item(ai_item, match, profile, overrides,
                                      item_id=old["itemId"] if old else None, grams_override=grams)

    # ======================= confirm / history / search =======================

    async def confirm(self, user_id: str, *, analysis_id: str, meal_type: str | None, logged_at: datetime | None) -> dict:
        analysis = await self._own_pending(user_id, analysis_id)
        if not analysis["items"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The meal has no items.")
        when = (logged_at or datetime.now(timezone.utc)).astimezone(IST)
        meal_type = meal_type or analysis.get("mealType") or _guess_meal_type(when)
        if not self.settings.store_details:
            # Privacy mode: dish names, amounts and six nutrients; deleted after history_days.
            items = [{"itemId": item["itemId"], "name": item["name"], "quantity": item["quantity"],
                      "unit": item["unit"], "grams": item["grams"],
                      "nutrients": {k: v for k, v in item["nutrients"].items() if k in nx.DIARY_NUTRIENTS}}
                     for item in analysis["items"]]
            meal = {
                "id": uuid.uuid4().hex, "userId": user_id, "mealType": meal_type,
                "loggedAt": when.isoformat(), "date": when.date().isoformat(),
                "items": items, "totals": nx.add_totals(items), "createdAt": now_iso(),
                "expireAt": _day_start(when.date() + timedelta(days=self.settings.history_days)).isoformat(),
            }
            await self.store.put(MEALS, meal)
            await self.store.delete(ANALYSES, analysis_id)  # the scan is not kept once saved
            return meal

        keep = ("itemId", "name", "aiName", "originalAiName", "quantity", "unit", "grams", "source", "match",
                "nutrients", "edited")
        items = [{k: item.get(k) for k in keep} for item in analysis["items"]]
        meal = {
            "id": uuid.uuid4().hex, "userId": user_id, "analysisId": analysis_id,
            "mealType": meal_type,
            "loggedAt": when.isoformat(), "date": when.date().isoformat(),
            "inputKind": analysis["inputKind"], "items": items, "totals": nx.add_totals(items),
            "createdAt": now_iso(),
        }
        await self.store.put(MEALS, meal)
        analysis.update(status="confirmed", mealId=meal["id"], updatedAt=now_iso())
        await self.store.put(ANALYSES, analysis)
        return meal

    async def history(self, user_id: str, *, date: str | None, days: int) -> dict:
        today = datetime.now(IST).date()
        end = min(datetime.fromisoformat(date).date(), today) if date else today
        start = end - timedelta(days=days - 1)
        if not self.settings.store_details:
            # Never show more than history_days back, even if MongoDB hasn't deleted it yet.
            start = max(start, today - timedelta(days=self.settings.history_days - 1))
        meals = [m for m in await self.store.find(MEALS, {"userId": user_id}, limit=2000)
                 if start.isoformat() <= m["date"] <= end.isoformat()]
        by_day: dict[str, list[dict]] = {}
        for meal in sorted(meals, key=lambda m: m["loggedAt"]):
            by_day.setdefault(meal["date"], []).append(meal)
        return {
            "from": start.isoformat(), "to": end.isoformat(),
            "days": [{"date": day, "meals": day_meals, "totals": nx.add_totals(
                [{"nutrients": m["totals"]} for m in day_meals])} for day, day_meals in sorted(by_day.items(), reverse=True)],
        }

    async def delete_meal(self, user_id: str, meal_id: str) -> None:
        meal = await self.store.get(MEALS, meal_id)
        if not meal or meal["userId"] != user_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Meal not found.")
        await self.store.delete(MEALS, meal_id)

    async def delete_user_data(self, user_id: str) -> int:
        """Remove everything stored for this user (call from the host app's account deletion)."""
        removed = 0
        for collection in ALL_COLLECTIONS:
            docs = await self.store.find(collection, {"userId": user_id}, limit=100_000)
            if collection == PROFILES and (profile := await self.store.get(collection, user_id)):
                docs.append(profile)
            for doc in docs:
                await self.store.delete(collection, doc["id"])
                removed += 1
        return removed

    def search(self, query: str, limit: int) -> list[dict]:
        if not self.settings.use_database:
            return []
        results = []
        for c in self.matcher.candidates([query], limit=limit):
            food = self.db.by_code[c["foodCode"]]
            results.append({**c, "kcalPer100g": food["per100g"].get("energy_kcal"),
                            "serving": food["serving"], "quality": food["quality"]})
        return results

    # ======================= helpers =======================

    async def _own_pending(self, user_id: str, analysis_id: str) -> dict:
        analysis = await self.store.get(ANALYSES, analysis_id)
        if not analysis or analysis["userId"] != user_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found.")
        if analysis["status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, "This meal was already saved.")
        return analysis

    def public(self, analysis: dict) -> dict:
        """What the app receives (internal fields removed)."""
        hidden = {"searchTerms", "per100g", "aiEstimatePer100g"}
        items = []
        for item in analysis["items"]:
            out = {k: v for k, v in item.items() if k not in hidden}
            # The AI's own numbers, shown next to database values so the user can compare.
            estimate = item.get("aiEstimatePer100g")
            out["aiEstimate"] = nx.scale(estimate, item["grams"]) if estimate and item["source"] == "database" else None
            items.append(out)
        return {
            "analysisId": analysis["id"], "status": analysis["status"], "notFood": False,
            "inputKind": analysis["inputKind"], "transcript": analysis.get("transcript", ""),
            "mealType": analysis.get("mealType"), "items": items, "totals": nx.add_totals(items),
            "needsReview": any(i["needsReview"] for i in items), "warnings": analysis.get("warnings", []),
            "createdAt": analysis["createdAt"],
            # False -> the app should hide "pick from our food list" and only offer typing a name.
            "databaseEnabled": self.settings.use_database,
        }


def _no_match() -> dict:
    """Match result for "not looked up in the database"."""
    return {"foodCode": None, "score": 0.0, "method": "none", "viaMainName": False, "candidates": []}


def _day_start(day) -> datetime:
    """Midnight IST at the start of `day`."""
    return datetime(day.year, day.month, day.day, tzinfo=IST)


def _guess_meal_type(when: datetime) -> str:
    hour = when.hour
    if 5 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 16:
        return "lunch"
    if 16 <= hour < 19:
        return "snack"
    return "dinner"
