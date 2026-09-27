"""Vision-assisted inventory extraction for uploaded pantry photos.

The integration intentionally returns suggestions only.  The web app matches
those suggestions against its own food database and asks the user to confirm
them before anything is written to the pantry.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
MAX_SUGGESTIONS = 60


def _json_object(text: str) -> dict[str, Any]:
    """Decode a JSON object, tolerating a fenced model response."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("The image service returned an unreadable result.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("The image service returned an invalid result.")
    return value


def _request_json(url: str, payload: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = detail.get("error", {}).get("message")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            message = None
        raise RuntimeError(message or f"Image service failed (HTTP {exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("Could not reach the image analysis service.") from exc


def analyze_inventory_image(
    image: bytes,
    media_type: str,
    current_inventory: list[dict],
) -> list[dict]:
    """Return food candidates visible in an image using a vision model.

    Configure with ``LETTUCEREMIND_VISION_API_KEY`` (or ``OPENAI_API_KEY``).
    The endpoint and model can be overridden for any OpenAI-compatible
    service with ``LETTUCEREMIND_VISION_URL`` and
    ``LETTUCEREMIND_VISION_MODEL``.
    """
    api_key = (
        os.environ.get("LETTUCEREMIND_VISION_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "Photo inventory needs OPENAI_API_KEY (or "
            "LETTUCEREMIND_VISION_API_KEY) on the server."
        )

    inventory_text = json.dumps(current_inventory, separators=(",", ":"))
    prompt = (
        "Identify distinct grocery or food items visibly present in this pantry, "
        "fridge, or freezer photo. Use generic food names rather than brands "
        "(for example, 'marinara sauce', not 'Rao's'). Estimate package counts "
        "only when clearly visible. Do not infer hidden items and do not include "
        "shelves, containers, or appliances. Return only JSON in this form: "
        '{"items":[{"name":"milk","quantity":1,"confidence":0.95}]}. '
        f"The current inventory is included only for reconciliation: {inventory_text}"
    )
    data_url = (
        f"data:{media_type};base64,"
        + base64.b64encode(image).decode("ascii")
    )
    payload = {
        "model": os.environ.get("LETTUCEREMIND_VISION_MODEL", DEFAULT_MODEL),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
    }
    response = _request_json(
        os.environ.get("LETTUCEREMIND_VISION_URL", DEFAULT_ENDPOINT),
        payload,
        api_key,
    )
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("The image service returned an invalid result.") from exc
    result = _json_object(content)
    items = result.get("items", [])
    if not isinstance(items, list):
        raise RuntimeError("The image service returned an invalid item list.")
    return [item for item in items[:MAX_SUGGESTIONS] if isinstance(item, dict)]
