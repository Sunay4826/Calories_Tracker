"""Where analyses, meal logs, corrections and learning data are saved.

Two interchangeable stores with the same four methods (get / put / find / delete):
- JsonFileStore  one JSON file per collection on disk. For local testing only.
- MongoStore     MongoDB via motor (production). Connects with MONGODB_URI / MONGODB_DB.

Every document has a string "id". MongoStore stores it as "_id".

Auto-delete: a document may carry "expireAt" (ISO date-time text). Both stores hide it
once that time has passed. MongoStore saves it as a real Date and creates a TTL index,
so MongoDB itself deletes it (within about a minute); JsonFileStore purges on write.
"""
import asyncio
import json
from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from .config import get_food_settings

# Collection names (also listed in docs/08-deployment.md).
ANALYSES = "food_analyses"          # one per photo/text/voice request, editable until confirmed
MEALS = "food_meal_logs"            # confirmed meals = the user's food diary
CORRECTIONS = "food_corrections"    # every fix a user made (audit trail + learning input)
PROFILES = "food_user_profiles"     # per-user trust score, personal aliases and portions
ALIASES = "food_aliases"            # nutritionist-approved global name -> food code
REVIEW = "food_review_queue"        # items waiting for a nutritionist decision
OVERRIDES = "food_overrides"        # database rows a nutritionist marked unreliable

ALL_COLLECTIONS = [ANALYSES, MEALS, CORRECTIONS, PROFILES, ALIASES, REVIEW, OVERRIDES]


TTL_COLLECTIONS = (ANALYSES, MEALS)  # collections whose documents carry expireAt


def _expired(doc: dict) -> bool:
    expire_at = doc.get("expireAt")
    if not expire_at:
        return False
    if isinstance(expire_at, str):
        expire_at = datetime.fromisoformat(expire_at)
    if expire_at.tzinfo is None:  # MongoDB returns naive UTC datetimes
        expire_at = expire_at.replace(tzinfo=timezone.utc)
    return expire_at <= datetime.now(timezone.utc)


class Store(Protocol):
    async def get(self, collection: str, doc_id: str) -> dict | None: ...
    async def put(self, collection: str, doc: dict) -> None: ...
    async def find(self, collection: str, filters: dict[str, Any] | None = None, limit: int = 500) -> list[dict]: ...
    async def delete(self, collection: str, doc_id: str) -> None: ...


class JsonFileStore:
    """Keeps every collection in memory and rewrites its file on each change."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, dict[str, dict]] = {}
        self.lock = asyncio.Lock()

    def _collection(self, name: str) -> dict[str, dict]:
        if name not in self.data:
            path = self.folder / f"{name}.json"
            self.data[name] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return self.data[name]

    def _save(self, name: str) -> None:
        path = self.folder / f"{name}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data[name], ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    async def get(self, collection, doc_id):
        doc = self._collection(collection).get(doc_id)
        return json.loads(json.dumps(doc)) if doc and not _expired(doc) else None

    async def put(self, collection, doc):
        async with self.lock:
            docs = self._collection(collection)
            for old_id in [i for i, d in docs.items() if _expired(d)]:
                del docs[old_id]  # same job as MongoDB's TTL index
            docs[doc["id"]] = json.loads(json.dumps(doc))
            self._save(collection)

    async def find(self, collection, filters=None, limit=500):
        docs = [
            doc for doc in self._collection(collection).values()
            if not _expired(doc) and all(doc.get(k) == v for k, v in (filters or {}).items())
        ]
        docs.sort(key=lambda d: d.get("createdAt", ""), reverse=True)
        return json.loads(json.dumps(docs[:limit]))

    async def delete(self, collection, doc_id):
        async with self.lock:
            if self._collection(collection).pop(doc_id, None) is not None:
                self._save(collection)


class MongoStore:
    def __init__(self, get_collection: Callable[[str], Any]):
        self.get_collection = get_collection
        self.ttl_ready: set[str] = set()

    @staticmethod
    def _out(doc: dict | None) -> dict | None:
        if doc is None or _expired(doc):  # TTL deletion can lag up to ~60 s
            return None
        doc = dict(doc)
        doc["id"] = str(doc.pop("_id"))
        if isinstance(doc.get("expireAt"), datetime):
            doc["expireAt"] = doc["expireAt"].replace(tzinfo=timezone.utc).isoformat()
        return doc

    async def _ensure_ttl(self, collection: str) -> None:
        if collection in TTL_COLLECTIONS and collection not in self.ttl_ready:
            # MongoDB deletes each document once its expireAt Date has passed.
            await self.get_collection(collection).create_index("expireAt", expireAfterSeconds=0)
            self.ttl_ready.add(collection)

    async def get(self, collection, doc_id):
        return self._out(await self.get_collection(collection).find_one({"_id": doc_id}))

    async def put(self, collection, doc):
        await self._ensure_ttl(collection)
        body = {k: v for k, v in doc.items() if k != "id"}
        if isinstance(body.get("expireAt"), str):
            body["expireAt"] = datetime.fromisoformat(body["expireAt"]).astimezone(timezone.utc)
        await self.get_collection(collection).replace_one({"_id": doc["id"]}, body, upsert=True)

    async def find(self, collection, filters=None, limit=500):
        cursor = self.get_collection(collection).find(filters or {}).sort("createdAt", -1).limit(limit)
        return [out async for doc in cursor if (out := self._out(doc))]

    async def delete(self, collection, doc_id):
        await self.get_collection(collection).delete_one({"_id": doc_id})


@lru_cache
def get_store() -> Store:
    settings = get_food_settings()
    if settings.store == "json":
        return JsonFileStore(settings.json_store_dir)
    from motor.motor_asyncio import AsyncIOMotorClient

    database = AsyncIOMotorClient(settings.mongodb_uri)[settings.mongodb_db]
    return MongoStore(lambda name: database[name])
