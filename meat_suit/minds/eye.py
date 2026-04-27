"""Mind 4: The Eye. Photo-to-data.

Two-stage pipeline:
  1. Vision pass: GPT-4o looks at the image and returns a neutral observation +
     structured signals (clutter level, food state, lighting, vibes).
  2. Persona pass (handled in bot.py): the active Persona reacts to the neutral
     observation in-character.

This split is deliberate: the Persona never sees the structured signals (it stays
'unmasked'), and the Scribe gets clean signals to feed the Weaver.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from openai import AsyncOpenAI


EYE_SYSTEM_PROMPT = """
You are The Eye — a neutral visual analyst. The user (AuDHD, alexithymia) sends
you photos from their daily life: messy desk, half-eaten food, sky, pet, sticky
note. You produce two things:

1. A short, neutral, NON-JUDGMENTAL observation (1-2 sentences) describing what
   you see in plain terms. No therapy-speak. No "I sense..." No emotion-naming.
   Examples:
     "A desk surface with three coffee cups, an open laptop, and scattered papers."
     "A bowl with about half a portion of pasta remaining."
     "A window showing late-afternoon light, orange-pink."

2. A structured JSON of signals you can confidently extract.

Return ONLY JSON in this shape:
{
  "observation": "1-2 sentence neutral description",
  "signals": {
    "scene_type": "desk|food|outdoors|pet|object|self|other",
    "clutter_level": "tidy|moderate|cluttered|chaotic|unknown",
    "food_state": "untouched|partial|eaten|none|unknown",
    "caffeine_vessels_visible": 0,
    "water_vessels_visible": 0,
    "lighting": "bright|dim|natural|artificial|unknown",
    "time_of_day_guess": "morning|midday|afternoon|evening|night|unknown",
    "interest_topics": ["short phrases of things in the image worth noting"],
    "executive_function_proxy": "tidy|moderate|trash_fire|unknown"
  }
}

If you cannot tell, use "unknown". Never invent. Never moralize. Never address
the user directly. JSON only.
""".strip()


async def see(
    client: AsyncOpenAI,
    *,
    model: str,
    image_bytes: bytes,
    caption: str | None,
) -> dict[str, Any]:
    """Run the Eye on a single image. Returns dict with 'observation' + 'signals'."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    user_content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]
    if caption:
        user_content.append({"type": "text", "text": f"User caption: {caption}"})
    user_content.append({"type": "text", "text": "Return JSON only."})

    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EYE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        response_format={"type": "json_object"},
        max_tokens=500,
    )
    raw = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"observation": "(image received, parse failure)", "signals": {}}
    data.setdefault("observation", "(no observation)")
    data.setdefault("signals", {})
    return data
