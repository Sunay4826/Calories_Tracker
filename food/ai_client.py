"""Sends a prompt (+ optional photo / voice note) to the AI and returns its JSON.

Two providers, picked by FOOD_AI_PROVIDER:
- GeminiClient       Google Gemini REST API. Supports photo AND audio.
- BedrockClaudeClient Claude on AWS Bedrock (useful if your backend already runs on AWS and uses Bedrock for the
                      other LLM features). Supports photo, NOT audio, so with Bedrock the
                      app must turn speech into text on the phone first.

Both retry busy/limit errors (429, 500, 503, 529) after 1 s, 2 s and 4 s, the
same schedule fud-ai uses.
"""
import asyncio
import json
import re
from functools import lru_cache
from typing import Protocol

import httpx

from .config import FoodSettings, get_food_settings

RETRY_STATUS = {429, 500, 503, 529}
RETRY_DELAYS = (1, 2, 4)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class AiError(Exception):
    """The AI could not be reached or did not return usable JSON."""


Media = tuple[str, str]  # (mime type, base64 data)


class AiClient(Protocol):
    async def generate_json(self, prompt: str, media: list[Media] | None = None) -> dict: ...


def extract_json(text: str) -> dict:
    """Parse the AI's reply even if wrapped in ``` fences or extra words."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        depth = 0
        value = None
        for i in range(max(start, 0), len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        value = json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        pass
                    break
    if not isinstance(value, dict) and start >= 0:
        # Long replies (busy thalis) sometimes drop a closing brace: `...}]}` instead of `...}}]}`.
        try:
            value = json.loads(_repair_brackets(cleaned[start:]))
        except json.JSONDecodeError:
            value = None
    if not isinstance(value, dict):
        raise AiError("The AI reply was not valid JSON.")
    return value


def _repair_brackets(text: str) -> str:
    """Insert missing } / ] where a closer doesn't match, and close anything left open."""
    pairs, out, stack, in_string, escaped = {"{": "}", "[": "]"}, [], [], False, False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in pairs:
            stack.append(pairs[ch])
        elif ch in "}]":
            while stack and stack[-1] != ch:
                out.append(stack.pop())
            if not stack:
                break  # stray closer after the top-level object: ignore the rest
            stack.pop()
        out.append(ch)
        if not stack and out and out[-1] in "}]":
            break
    return "".join(out) + "".join(reversed(stack))


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout: float):
        if not api_key:
            raise AiError("GEMINI_API_KEY is not set.")
        self.api_key, self.model, self.timeout = api_key, model, timeout

    async def generate_json(self, prompt: str, media: list[Media] | None = None) -> dict:
        parts: list[dict] = [{"text": prompt}]
        for mime, data in media or []:
            parts.append({"inline_data": {"mime_type": mime, "data": data}})
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0, "seed": 7},
        }
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.api_key}
        url = GEMINI_URL.format(model=self.model)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(len(RETRY_DELAYS) + 1):
                try:
                    response = await client.post(url, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    if attempt < len(RETRY_DELAYS):
                        await asyncio.sleep(RETRY_DELAYS[attempt])
                        continue
                    raise AiError(f"Gemini unreachable: {exc}") from exc
                if response.status_code in RETRY_STATUS and attempt < len(RETRY_DELAYS):
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                if response.status_code != 200:
                    raise AiError(f"Gemini HTTP {response.status_code}: {response.text[:300]}")
                data = response.json()
                try:
                    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
                except (KeyError, IndexError) as exc:
                    raise AiError("Gemini returned no answer (possibly blocked by safety filters).") from exc
                try:
                    return extract_json(text)
                except AiError:
                    if attempt < len(RETRY_DELAYS):
                        continue  # broken JSON is random: asking again usually works
                    raise
        raise AiError("Gemini is busy, please try again.")


class BedrockClaudeClient:
    """Claude via AWS Bedrock. Uses the standard AWS credential chain (IAM role or env vars).

    NOTE: written for a typical Bedrock setup but not run
    locally (needs AWS credentials). Test it on the dev stack before enabling.
    """

    def __init__(self, model_id: str, region: str):
        if not model_id:
            raise AiError("FOOD_BEDROCK_MODEL_ID is not set.")
        import boto3  # optional dependency, only needed for FOOD_AI_PROVIDER=bedrock

        self.model_id = model_id
        self.client = boto3.client("bedrock-runtime", region_name=region)

    async def generate_json(self, prompt: str, media: list[Media] | None = None) -> dict:
        content: list[dict] = []
        for mime, data in media or []:
            if not mime.startswith("image/"):
                raise AiError("Voice notes need FOOD_AI_PROVIDER=gemini; send speech as text instead.")
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        content.append({"type": "text", "text": prompt})
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
        })

        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                response = await asyncio.to_thread(self.client.invoke_model, modelId=self.model_id, body=body)
                payload = json.loads(response["body"].read())
                text = "".join(block.get("text", "") for block in payload.get("content", []))
                return extract_json(text)
            except AiError:
                raise
            except Exception as exc:  # botocore throttling / service errors
                if attempt < len(RETRY_DELAYS):
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                raise AiError(f"Bedrock call failed: {exc}") from exc
        raise AiError("Bedrock is busy, please try again.")


@lru_cache
def get_ai_client() -> AiClient:
    settings: FoodSettings = get_food_settings()
    if settings.ai_provider == "bedrock":
        return BedrockClaudeClient(settings.bedrock_model_id, settings.bedrock_region)
    return GeminiClient(settings.gemini_api_key, settings.gemini_model, settings.ai_timeout_seconds)
