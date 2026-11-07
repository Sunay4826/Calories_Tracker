"""Tests for the food engine. Run:  .venv\\Scripts\\python -m pytest -q

A FakeAi stands in for Gemini, so tests are free, fast and repeatable.
"""
import asyncio
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(FOOD_AUTH_MODE="dev", FOOD_STORE="json")

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from food.ai_client import AiError, extract_json  # noqa: E402
from food.config import get_food_settings  # noqa: E402
from food.engine import FoodEngine  # noqa: E402
from food.food_db import get_food_db, load_seed_aliases  # noqa: E402
from food.learning import Learning  # noqa: E402
from food.matcher import Matcher  # noqa: E402
from food.parsing import parse_recognition  # noqa: E402
from food.prompts import recognition_prompt  # noqa: E402
from food.ratelimit import limiter  # noqa: E402
from food.nutrients import DIARY_NUTRIENTS  # noqa: E402
from food.store import ALIASES, ANALYSES, CORRECTIONS, MEALS, PROFILES, REVIEW, JsonFileStore  # noqa: E402

DB = get_food_db()
MATCHER = Matcher(DB, load_seed_aliases())


def code_of(name: str) -> str:
    return next(f["code"] for f in DB.foods if f["name"] == name)


def item(name, grams, kcal, quantity=1, unit="piece", terms=(), confidence=0.9):
    return {"name": name, "search_terms": list(terms), "quantity": quantity, "unit": unit, "grams": grams,
            "confidence": confidence, "alternatives": [], "estimate": {"energy_kcal": kcal, "protein_g": 3,
            "carb_g": 20, "fat_g": 4}}


class FakeAi:
    """Returns canned replies: recognition items, a choose answer, or a correction item."""

    def __init__(self):
        self.items: list[dict] = []
        self.choose: dict[int, str | None] = {}
        self.correction: dict | None = None
        self.calls: list[str] = []

    async def generate_json(self, prompt, media=None):
        if "Entries:" in prompt:
            self.calls.append("choose")
            return {"choices": [{"index": i, "code": c} for i, c in self.choose.items()]}
        if "A user is fixing" in prompt:
            self.calls.append("correction")
            return {"items": [self.correction]}
        self.calls.append("recognize")
        return {"not_food": not self.items, "items": self.items}


def make_engine(tmp_path, use_database: bool, store_details: bool = True):
    limiter.hits.clear()
    settings = replace(get_food_settings(), use_database=use_database, store_details=store_details)
    store = JsonFileStore(tmp_path)
    ai = FakeAi()
    engine = FoodEngine(db=DB, matcher=MATCHER, ai=ai, store=store,
                        learning=Learning(store, DB, settings), settings=settings)
    return engine, ai, store


@pytest.fixture
def setup(tmp_path):
    """Database + AI (FOOD_USE_DATABASE=true)."""
    return make_engine(tmp_path, use_database=True)


@pytest.fixture
def ai_only(tmp_path):
    """AI estimate only (FOOD_USE_DATABASE=false)."""
    return make_engine(tmp_path, use_database=False)


@pytest.fixture
def private(tmp_path):
    """The production defaults: AI only + privacy mode (FOOD_STORE_DETAILS=false)."""
    return make_engine(tmp_path, use_database=False, store_details=False)


def run(coro):
    return asyncio.run(coro)


FAKE_PHOTO = "ZmFrZS1qcGVn"  # base64 of b"fake-jpeg"; the FakeAi never looks at it


def analyze(engine, user="9876543210", text="lunch", photo=False):
    return run(engine.analyze(user, text=None if photo else text, image_b64=FAKE_PHOTO if photo else None,
                              image_mime="image/jpeg" if photo else None,
                              audio_b64=None, audio_mime=None, meal_type=None))


# ---------------- AI-only mode (FOOD_USE_DATABASE=false) ----------------

def test_ai_only_uses_ai_estimate_and_skips_database(ai_only):
    engine, ai, store = ai_only
    ai.items = [item("Idli", 120, 160, quantity=3), item("Khaman dhokla", 120, 190, quantity=4)]
    result = analyze(engine)
    assert result["databaseEnabled"] is False
    assert ai.calls == ["recognize"]  # no "choose" call
    for got, kcal in zip(result["items"], (160, 190)):
        assert got["source"] == "ai_estimate" and got["match"] is None and got["alternatives"] == []
        assert got["nutrients"]["energy_kcal"] == pytest.approx(kcal)
        assert got["reviewReasons"] == []  # an AI estimate is expected here, not a warning
    assert run(store.find(REVIEW, {"type": "data_conflict"})) == []  # dhokla not compared with INDB


def test_ai_only_amount_change_and_typed_fix(ai_only):
    engine, ai, _ = ai_only
    ai.items = [item("Paratha", 100, 300, unit="piece")]
    result = analyze(engine)
    item_id = result["items"][0]["itemId"]
    fixed = run(engine.fix("9876543210", analysis_id=result["analysisId"], action="set_amount", item_id=item_id,
                           food_code=None, corrected_name=None, grams=150, quantity=None, unit=None))
    assert fixed["items"][0]["nutrients"]["energy_kcal"] == pytest.approx(450)

    ai.correction = item("Methi thepla", 100, 280, terms=["thepla"])
    fixed = run(engine.fix("9876543210", analysis_id=result["analysisId"], action="replace", item_id=item_id,
                           food_code=None, corrected_name="methi thepla", grams=None, quantity=None, unit=None))
    thepla = fixed["items"][0]
    assert thepla["match"] is None and thepla["source"] == "ai_estimate" and thepla["name"] == "Methi thepla"
    assert "correction" in ai.calls


def test_ai_only_rejects_food_codes_and_search_is_empty(ai_only):
    engine, ai, _ = ai_only
    ai.items = [item("Idli", 120, 160, quantity=3)]
    result = analyze(engine)
    with pytest.raises(HTTPException) as err:
        run(engine.fix("9876543210", analysis_id=result["analysisId"], action="replace",
                       item_id=result["items"][0]["itemId"], food_code=code_of("Idli"), corrected_name=None,
                       grams=None, quantity=None, unit=None))
    assert err.value.status_code == 400
    assert engine.search("idli", 10) == []


# ---------------- privacy mode (FOOD_STORE_DETAILS=false) ----------------

def fix_call(engine, analysis_id, action, item_id, **kw):
    args = dict(food_code=None, corrected_name=None, grams=None, quantity=None, unit=None) | kw
    return run(engine.fix("9876543210", analysis_id=analysis_id, action=action, item_id=item_id, **args))


def confirm_call(engine, analysis_id, logged_at=None):
    return run(engine.confirm("9876543210", analysis_id=analysis_id, meal_type=None, logged_at=logged_at))


def test_private_scan_keeps_no_text_hash_or_learning(private):
    engine, ai, store = private
    ai.items = [item("Paratha", 100, 300, unit="piece")]
    result = run(engine.analyze("9876543210", text="my secret note: 1 paratha", image_b64=FAKE_PHOTO,
                                image_mime="image/jpeg", audio_b64=None, audio_mime=None, meal_type=None))
    pending = run(store.get(ANALYSES, result["analysisId"]))
    assert "userText" not in pending and "imageSha256" not in pending and "transcript" not in pending
    assert pending["expireAt"]  # unsaved scans disappear by themselves

    item_id = result["items"][0]["itemId"]
    fix_call(engine, result["analysisId"], "set_amount", item_id, quantity=2, unit="piece")
    ai.correction = item("Methi thepla", 100, 280)
    fix_call(engine, result["analysisId"], "replace", item_id, corrected_name="methi thepla")
    for collection in (CORRECTIONS, PROFILES, REVIEW):
        assert run(store.find(collection)) == []  # nothing learned


def test_private_meal_keeps_names_and_six_nutrients_and_deletes_scan(private):
    engine, ai, store = private
    ai.items = [item("Idli", 120, 160, quantity=3), item("Sambar", 150, 90, unit="katori")]
    result = analyze(engine)
    meal = confirm_call(engine, result["analysisId"])
    assert [i["name"] for i in meal["items"]] == ["Idli", "Sambar"]
    assert set(meal["totals"]) <= set(DIARY_NUTRIENTS) and meal["totals"]["energy_kcal"] == pytest.approx(250)
    assert "carb_g" not in meal["totals"] and "analysisId" not in meal
    assert run(store.get(ANALYSES, result["analysisId"])) is None  # scan deleted on save

    logged = datetime.fromisoformat(meal["loggedAt"])
    assert datetime.fromisoformat(meal["expireAt"]) == datetime(
        logged.year, logged.month, logged.day, tzinfo=logged.tzinfo) + timedelta(days=30)


def test_private_history_shows_only_last_30_days_and_expired_rows_vanish(private):
    engine, ai, store = private
    ai.items = [item("Idli", 120, 160, quantity=3)]
    now = datetime.now(timezone.utc)
    for days_ago in (0, 29, 30, 45):
        confirm_call(engine, analyze(engine)["analysisId"], logged_at=now - timedelta(days=days_ago))
    days = run(engine.history("9876543210", date=None, days=90))["days"]
    # 30 and 45 days ago are already past their expireAt -> gone; 29 days ago is the last day kept.
    assert len(days) == 2

    old = run(store.find(MEALS))[0]
    old["expireAt"] = (now - timedelta(seconds=1)).isoformat()
    run(store.put(MEALS, old))
    assert old["id"] not in {m["id"] for m in run(store.find(MEALS))}


def test_pending_scan_expires(private):
    engine, ai, store = private
    ai.items = [item("Idli", 120, 160, quantity=3)]
    analysis_id = analyze(engine)["analysisId"]
    pending = run(store.get(ANALYSES, analysis_id))
    pending["expireAt"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    run(store.put(ANALYSES, pending))
    with pytest.raises(HTTPException) as err:
        confirm_call(engine, analysis_id)
    assert err.value.status_code == 404


def test_delete_user_data_removes_everything(setup):
    engine, ai, store = setup
    ai.items = [item("Idli", 120, 160, quantity=3)]
    result = analyze(engine)
    fix_call(engine, result["analysisId"], "set_amount", result["items"][0]["itemId"], quantity=4, unit="piece")
    confirm_call(engine, result["analysisId"])
    assert run(engine.delete_user_data("9876543210")) >= 4  # analysis, meal, correction, profile
    for collection in (ANALYSES, MEALS, CORRECTIONS):
        assert run(store.find(collection, {"userId": "9876543210"})) == []
    assert run(store.get(PROFILES, "9876543210")) is None


# ---------------- database + matching ----------------

def test_database_built_and_suspect_rows_flagged():
    assert len(DB.foods) == 1014
    assert DB.get(code_of("Poori"))["quality"] == "suspect"
    assert DB.get(code_of("Idli"))["quality"] == "ok"
    assert DB.get(code_of("Mayonnaise"))["quality"] == "ok"  # naturally fatty, not an error


@pytest.mark.parametrize("query,expected", [
    ("khaman dhokla", "Khaman (dhokla)"),
    ("idly", "Idli"),
    ("phulka", "Chapati/Roti"),
    ("palak paneer", "Spinach paneer (Palak paneer)"),
    ("sambhar", "Sambar"),
])
def test_matcher(query, expected):
    result = MATCHER.match([query])
    assert DB.get(result["foodCode"])["name"] == expected
    assert result["score"] >= 0.86 or result["method"] == "alias"


@pytest.mark.parametrize("raw", [
    # Seen live on a busy thali photo: the last item's closing brace was missing.
    '{"items": [{"name": "Rice", "estimate": {"energy_kcal": 195}}, {"name": "Avial", "estimate": {"energy_kcal": 95}]}',
    '```json\n{"items": [{"name": "Rice"}, {"name": "Dal"]}\n```',
    '{"items": [{"name": "Rice } ] tricky", "estimate": {"energy_kcal": 1}}]',  # brackets inside text, cut off
])
def test_ai_json_with_missing_braces_is_repaired(raw):
    items = extract_json(raw)["items"]
    assert items[0]["name"].startswith("Rice") and len(items) in (1, 2)


def test_ai_json_that_is_not_json_still_fails():
    with pytest.raises(AiError):
        extract_json("Sorry, I can't help with that.")


def test_photo_counting_rules_only_for_photos():
    photo = recognition_prompt(has_image=True, has_audio=False, user_text=None)
    text = recognition_prompt(has_image=False, has_audio=False, user_text="2 roti")
    assert "Photo rules" in photo and "Count pieces you can actually see" in photo
    assert "Photo rules" not in text and "<<<2 roti>>>" in text


def test_parsing_rejects_impossible_values():
    rec = parse_recognition({"items": [
        {"name": "Ghee rice", "grams": 100, "estimate": {"energy_kcal": 2000}},
        {"name": "Huge roti", "grams": 5000},
        {"name": ""},
    ]}, max_items=15)
    assert [i.name for i in rec.items] == ["Ghee rice", "Huge roti"]
    assert rec.items[0].estimate is None          # > 9 kcal per gram dropped
    assert rec.items[1].grams is None             # 5 kg ignored
    assert len(rec.warnings) == 3


# ---------------- analyze ----------------

def test_database_match_scales_per_100g(setup):
    engine, ai, _ = setup
    ai.items = [item("Idli", 120, 160, quantity=3)]
    result = analyze(engine)
    idli = result["items"][0]
    per100 = DB.get(code_of("Idli"))["per100g"]["energy_kcal"]
    assert idli["source"] == "database"
    assert idli["nutrients"]["energy_kcal"] == pytest.approx(per100 * 1.2, abs=0.01)
    assert result["totals"]["energy_kcal"] == idli["nutrients"]["energy_kcal"]


def test_suspect_row_uses_ai_estimate(setup):
    engine, ai, _ = setup
    ai.items = [item("Poori", 60, 180, quantity=2)]
    poori = analyze(engine)["items"][0]
    assert poori["source"] == "ai_estimate"
    assert "database_value_unreliable" in poori["reviewReasons"]
    assert poori["nutrients"]["energy_kcal"] == pytest.approx(180)


def test_unknown_food_falls_back_to_ai(setup):
    engine, ai, _ = setup
    ai.items = [item("Banana", 120, 105, terms=["kela"])]
    ai.choose = {0: None}
    banana = analyze(engine)["items"][0]
    assert banana["source"] == "ai_estimate" and banana["match"] is None
    assert "not_in_database" in banana["reviewReasons"]
    assert "choose" in ai.calls


def test_unsure_match_is_resolved_by_ai_choice(setup):
    engine, ai, _ = setup
    target = code_of("Potato cauliflower (Aloo gobhi)")
    ai.items = [item("Aloo gobhi sabji", 150, 160, unit="katori")]
    candidates = MATCHER.match(["Aloo gobhi sabji"])["candidates"]
    assert any(c["foodCode"] == target for c in candidates)
    ai.choose = {0: target}
    result = analyze(engine)["items"][0]
    assert result["match"]["foodCode"] == target and result["match"]["method"] == "ai_choice"


def test_big_disagreement_flags_review_and_queues_data_check(setup):
    engine, ai, store = setup
    ai.items = [item("Khaman dhokla", 120, 190, quantity=4)]  # DB says 55 kcal/100 g
    dhokla = analyze(engine)["items"][0]
    assert "ai_db_disagree" in dhokla["reviewReasons"]
    queue = run(store.find(REVIEW, {"type": "data_conflict"}))
    assert queue and queue[0]["foodCode"] == code_of("Khaman (dhokla)")


def test_match_through_other_name_is_double_checked(setup):
    engine, ai, _ = setup
    ai.items = [item("Chicken biryani", 350, 600, unit="plate", terms=["chicken pulao"])]
    ai.choose = {0: None}  # the AI says pulao is not biryani
    biryani = analyze(engine)["items"][0]
    assert "choose" in ai.calls
    assert biryani["match"] is None and biryani["source"] == "ai_estimate"


def test_use_ai_estimate_instead_of_database(setup):
    engine, ai, _ = setup
    ai.items = [item("Khaman dhokla", 120, 190, quantity=4)]
    result = analyze(engine)
    dhokla = result["items"][0]
    assert dhokla["aiEstimate"]["energy_kcal"] == pytest.approx(190)
    fixed = run(engine.fix("9876543210", analysis_id=result["analysisId"], action="use_estimate",
                           item_id=dhokla["itemId"], food_code=None, corrected_name=None,
                           grams=None, quantity=None, unit=None))
    after = fixed["items"][0]
    assert after["source"] == "ai_estimate" and after["nutrients"]["energy_kcal"] == pytest.approx(190)
    assert "ai_db_disagree" not in after["reviewReasons"]


def test_not_food(setup):
    engine, ai, _ = setup
    ai.items = []
    result = analyze(engine)
    assert result["notFood"] and result["analysisId"] is None


# ---------------- fix + personal learning ----------------

def test_set_amount_remembers_personal_portion(setup):
    engine, ai, _ = setup
    ai.items = [item("Phulka", 80, 240, quantity=2, unit="roti")]
    result = analyze(engine)
    roti = result["items"][0]
    fixed = run(engine.fix("9876543210", analysis_id=result["analysisId"], action="set_amount",
                           item_id=roti["itemId"], food_code=None, corrected_name=None,
                           grams=None, quantity=2, unit="roti"))
    assert fixed["items"][0]["grams"] == 80

    run(engine.fix("9876543210", analysis_id=result["analysisId"], action="set_amount",
                   item_id=roti["itemId"], food_code=None, corrected_name=None,
                   grams=60, quantity=2, unit="roti"))        # my rotis are 30 g each
    again = analyze(engine)["items"][0]
    assert again["gramsSource"] == "your_usual" and again["grams"] == 60


def test_replace_with_food_code_creates_personal_alias(setup):
    engine, ai, _ = setup
    moong = code_of("Washed moong dal (Dhuli moong ki dal)")
    ai.items = [item("Yellow dal", 150, 150, unit="katori")]
    ai.choose = {0: None}
    result = analyze(engine)
    fixed = run(engine.fix("9876543210", analysis_id=result["analysisId"], action="replace",
                           item_id=result["items"][0]["itemId"], food_code=moong, corrected_name=None,
                           grams=None, quantity=None, unit=None))
    assert fixed["items"][0]["match"]["foodCode"] == moong
    assert fixed["items"][0]["grams"] == 150                 # amount kept
    next_photo = analyze(engine, photo=True)["items"][0]
    assert next_photo["match"]["method"] == "personal_alias" and next_photo["note"]
    assert analyze(engine)["items"][0]["match"] is None      # typed "yellow dal": believe the user
    assert analyze(engine, user="9000000001", photo=True)["items"][0]["match"] is None  # other users unaffected


def test_typed_correction_uses_ai_then_database(setup):
    engine, ai, _ = setup
    ai.items = [item("Paratha", 100, 300, unit="piece")]
    result = analyze(engine)
    ai.correction = item("Methi thepla", 100, 280, terms=["thepla"])
    fixed = run(engine.fix("9876543210", analysis_id=result["analysisId"], action="replace",
                           item_id=result["items"][0]["itemId"], food_code=None,
                           corrected_name="it was gujarati methi thepla", grams=None, quantity=None, unit=None))
    assert fixed["items"][0]["match"]["foodCode"] == code_of("Methi thepla")


def test_remove_and_add_items(setup):
    engine, ai, _ = setup
    ai.items = [item("Idli", 80, 100, quantity=2), item("Sambar", 150, 90, unit="katori")]
    result = analyze(engine)
    after = run(engine.fix("9876543210", analysis_id=result["analysisId"], action="remove",
                           item_id=result["items"][1]["itemId"], food_code=None, corrected_name=None,
                           grams=None, quantity=None, unit=None))
    assert len(after["items"]) == 1
    after = run(engine.fix("9876543210", analysis_id=result["analysisId"], action="add", item_id=None,
                           food_code=code_of("Coconut chutney (Nariyal ki chutney)"), corrected_name=None,
                           grams=30, quantity=None, unit=None))
    assert len(after["items"]) == 2 and after["items"][1]["grams"] == 30


# ---------------- shared learning + fake data protection ----------------

def _correct(engine, ai, user, code):
    ai.items = [item("Kaman", 100, 160)]
    ai.choose = {0: None}
    result = analyze(engine, user=user)
    run(engine.fix(user, analysis_id=result["analysisId"], action="replace",
                   item_id=result["items"][0]["itemId"], food_code=code, corrected_name=None,
                   grams=None, quantity=None, unit=None))


def test_global_alias_needs_three_users_and_nutritionist(setup):
    engine, ai, store = setup
    dhokla = code_of("Dhokla")
    _correct(engine, ai, "9000000001", dhokla)
    _correct(engine, ai, "9000000002", dhokla)
    assert not run(store.find(REVIEW, {"type": "alias"}))          # 2 users: not enough
    _correct(engine, ai, "9000000003", dhokla)
    queue = run(store.find(REVIEW, {"type": "alias"}))
    assert queue and queue[0]["status"] == "pending"
    assert not run(store.find(ALIASES))                             # not global until approved

    run(engine.learning.decide(queue[0]["id"], "approve", "nutritionist-1"))
    ai.items = [item("Kaman", 100, 160)]
    match = analyze(engine, user="9000000099")["items"][0]["match"]
    assert match["foodCode"] == dhokla and match["method"] == "alias"
    assert run(engine.learning.get_profile("9000000001"))["trust"] == 0.6


def test_rejected_fake_corrections_lower_trust_and_stop_sharing(setup):
    engine, ai, store = setup
    wrong = code_of("Mayonnaise")
    for user in ("9000000011", "9000000012", "9000000013"):
        _correct(engine, ai, user, wrong)
    review_id = run(store.find(REVIEW, {"type": "alias"}))[0]["id"]
    run(engine.learning.decide(review_id, "reject", "nutritionist-1"))
    assert not run(store.find(ALIASES))
    profile = run(engine.learning.get_profile("9000000011"))
    assert profile["trust"] == 0.3

    # One more rejection pushes trust below 0.3 -> corrections stay personal only.
    profile["trust"] = 0.1
    run(store.put("food_user_profiles", profile))
    _correct(engine, ai, "9000000011", code_of("Dhokla"))
    latest = run(store.find("food_corrections", {"userId": "9000000011", "kind": "identity"}))[0]
    assert latest["status"] == "personal"


# ---------------- confirm / history / API ----------------

def test_confirm_history_and_double_confirm(setup):
    engine, ai, _ = setup
    ai.items = [item("Idli", 120, 160, quantity=3)]
    result = analyze(engine)
    meal = run(engine.confirm("9876543210", analysis_id=result["analysisId"], meal_type="breakfast", logged_at=None))
    assert meal["mealType"] == "breakfast" and meal["totals"]["energy_kcal"] > 0
    history = run(engine.history("9876543210", date=None, days=1))
    assert history["days"][0]["meals"][0]["id"] == meal["id"]
    with pytest.raises(Exception) as err:
        run(engine.confirm("9876543210", analysis_id=result["analysisId"], meal_type=None, logged_at=None))
    assert err.value.status_code == 409


def test_api_auth_and_admin(setup):
    engine, ai, _ = setup
    import main
    from food.api import get_engine

    main.app.dependency_overrides[get_engine] = lambda: engine
    client = TestClient(main.app)
    ai.items = [item("Idli", 120, 160, quantity=3)]
    body = {"phoneNumber": "9876543210", "text": "3 idli"}

    assert client.post("/api/auth/food/analyze", json=body).status_code == 401
    assert client.post("/api/auth/food/analyze", json=body,
                       headers={"Authorization": "Bearer dev-9000000000"}).status_code == 403
    ok = client.post("/api/auth/food/analyze", json=body, headers={"Authorization": "Bearer dev-9876543210"})
    assert ok.status_code == 200 and ok.json()["items"][0]["name"] == "Idli"

    assert client.get("/api/admin/food/review-queue", headers={"Authorization": "Bearer dev-9876543210"}).status_code == 403
    assert client.get("/api/admin/food/review-queue", headers={"Authorization": "Bearer dev-admin"}).status_code == 200
    main.app.dependency_overrides.clear()


def test_rate_limit(setup):
    engine, ai, _ = setup
    import main
    from food.api import get_engine

    main.app.dependency_overrides[get_engine] = lambda: engine
    client = TestClient(main.app)
    ai.items = [item("Idli", 40, 50)]
    headers = {"Authorization": "Bearer dev-9111111111"}
    codes = [client.post("/api/auth/food/analyze", json={"phoneNumber": "9111111111", "text": "idli"},
                         headers=headers).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    main.app.dependency_overrides.clear()
