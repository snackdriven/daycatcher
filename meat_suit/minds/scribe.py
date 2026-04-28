"""Mind 2: The Scribe. Asynchronously parses chat exhaust into JSON.

Runs periodically (default: every 30 min) AND once at end-of-day before the Weaver.
Reads `messages WHERE scribed=0` for today's local date, emits one extraction row,
and marks those messages scribed.
"""
from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI


SCRIBE_SYSTEM_PROMPT = """
You are The Scribe — a silent, invisible analyst. You read raw chat transcripts
between a user (who has AuDHD, alexithymia, and PDA) and a chatbot persona, and
you extract interoception + spoon-ledger signals into strict JSON.

You NEVER address the user. You NEVER produce prose. Your only output is one JSON
object matching the schema below. If a signal is not present in the chat, use null
or an empty list — never invent.

You read between the lines. The user has alexithymia, so direct emotional
statements are rare. Look for proxies:
- Hydration: complaints of headache, mention of coffee/water/dry eyes/being parched.
- Hunger: "haven't eaten," skipped lunch, mentions food, "starving," low energy.
- Bathroom: "haven't peed," "holding it," bladder mentions.
- Work drain / masking: descriptions of meetings, having to be "on," coworker
  interactions, performance fatigue, "smiling all day," dissociation.
- Social battery: mention of needing to be alone, dread about future social events,
  relief at solitude.
- Executive function: "waiting mode," staring at screen, can't start, paralysis,
  task switching difficulty, time blindness.
- Gratitudes: anything they noticed and found pleasant — small, solitary-adjacent
  joys count (a cat sat on them, the coffee was good, the sky was a color).

OUTPUT SCHEMA — return ONLY this JSON, no markdown fences, no commentary:
{
  "meat_suit_maintenance": {
    "hydration":  { "status": "good|low|unknown", "evidence": "short quote or null" },
    "hunger":     { "status": "satisfied|ignored|unknown", "evidence": "short quote or null" },
    "bathroom":   { "status": "ok|avoiding|unknown", "evidence": "short quote or null" }
  },
  "spoon_ledger": {
    "work_drain":          { "level": "low|moderate|high|extreme|unknown", "evidence": "..." },
    "social_battery":      { "level": "full|tapped|empty|unknown", "evidence": "..." },
    "executive_function":  { "state": "flowing|sluggish|waiting_mode|paralyzed|unknown", "evidence": "..." }
  },
  "mental_weather": "one short phrase capturing the day's emotional tone, or null",
  "gratitudes": ["short phrases of noticed micro-joys"]
}
""".strip()


async def extract(
    client: AsyncOpenAI,
    *,
    model: str,
    transcript_lines: list[str],
) -> dict[str, Any]:
    """Run a single Scribe pass on a batch of transcript lines."""
    if not transcript_lines:
        return _empty_payload()

    transcript = "\n".join(transcript_lines)
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SCRIBE_SYSTEM_PROMPT},
            {"role": "user", "content": f"TRANSCRIPT:\n{transcript}\n\nReturn JSON only."},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
        max_tokens=800,
    )
    raw = (resp.choices[0].message.content or "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return _empty_payload(error=raw[:200])


def _empty_payload(error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "meat_suit_maintenance": {
            "hydration": {"status": "unknown", "evidence": None},
            "hunger":    {"status": "unknown", "evidence": None},
            "bathroom":  {"status": "unknown", "evidence": None},
        },
        "spoon_ledger": {
            "work_drain":         {"level": "unknown", "evidence": None},
            "social_battery":     {"level": "unknown", "evidence": None},
            "executive_function": {"state": "unknown", "evidence": None},
        },
        "mental_weather": None,
        "gratitudes": [],
    }
    if error:
        payload["_parse_error"] = error
    return payload


TOPIC_SYSTEM_PROMPT = """
You label rabbit-hole entries. The user pasted a URL, a fact, or a half-thought
into their journaling bot. Your job: produce a SHORT topic phrase (2-6 words,
lowercase, no punctuation, no quotes) that captures what the thing is about.

Rules:
- 2 to 6 words. No more.
- Concrete subject. Examples: "18th-century clockmaking",
  "deep-sea bioluminescence", "roman concrete", "k-pop fan economics".
- If the URL host is informative but the path is opaque, you may use the host
  topic (e.g. "wikipedia article" is too vague — prefer "unknown wikipedia topic").
- If you genuinely cannot tell, return "miscellaneous curiosity".
- Return JSON only: {"topic": "..."}
""".strip()


async def topic_for(
    client,
    *,
    model: str,
    raw_text: str,
    url: str | None,
) -> str:
    """Extract a short topic phrase for a rabbit-hole entry. Best-effort — caller
    should be tolerant of fallback values."""
    snippet = (raw_text or "").strip()[:600]
    payload = f"URL: {url or '(none)'}\nText: {snippet}\n\nReturn JSON only."
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": TOPIC_SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
            max_tokens=60,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        topic = str(data.get("topic", "")).strip().lower()
    except Exception:
        topic = ""
    if not topic:
        return "miscellaneous curiosity"
    # Defensive trim: keep to ~6 words / 60 chars
    words = topic.split()
    if len(words) > 6:
        topic = " ".join(words[:6])
    return topic[:60]


def merge_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a day's worth of Scribe extractions into one consolidated picture.

    Strategy: latest non-unknown signal wins for status fields; gratitudes accumulate.
    """
    merged = _empty_payload()
    grats: list[str] = []
    weather_phrases: list[str] = []

    for p in payloads:
        ms = p.get("meat_suit_maintenance", {})
        for k in ("hydration", "hunger", "bathroom"):
            v = ms.get(k) or {}
            status = v.get("status")
            if status and status != "unknown":
                merged["meat_suit_maintenance"][k] = v

        sl = p.get("spoon_ledger", {})
        for k in ("work_drain", "social_battery", "executive_function"):
            v = sl.get(k) or {}
            level_or_state = v.get("level") or v.get("state")
            if level_or_state and level_or_state != "unknown":
                merged["spoon_ledger"][k] = v

        if p.get("mental_weather"):
            weather_phrases.append(str(p["mental_weather"]))
        for g in p.get("gratitudes") or []:
            if isinstance(g, str) and g.strip():
                grats.append(g.strip())

    merged["mental_weather"] = " · ".join(weather_phrases) if weather_phrases else None
    # de-dupe gratitudes preserving order
    seen: set[str] = set()
    merged["gratitudes"] = [g for g in grats if not (g.lower() in seen or seen.add(g.lower()))]
    return merged


def _format_status_line(label: str, entry: dict[str, Any]) -> str | None:
    """Format one status field (hydration/hunger/bathroom/work_drain/etc) if it
    has signal. Returns None for unknown/empty so callers can filter.
    """
    if not isinstance(entry, dict):
        return None
    state = entry.get("status") or entry.get("level") or entry.get("state")
    if not state or state == "unknown":
        return None
    evidence = entry.get("evidence") or entry.get("quote")
    if evidence:
        return f"  · {label}: {state} — {evidence}"
    return f"  · {label}: {state}"


def has_signal(merged: dict[str, Any]) -> bool:
    """True if a merged payload has any non-empty bucket worth showing."""
    ms = merged.get("meat_suit_maintenance", {})
    sl = merged.get("spoon_ledger", {})
    for k in ("hydration", "hunger", "bathroom"):
        v = ms.get(k) or {}
        if (v.get("status") or "unknown") != "unknown":
            return True
    for k in ("work_drain", "social_battery", "executive_function"):
        v = sl.get(k) or {}
        if (v.get("level") or v.get("state") or "unknown") != "unknown":
            return True
    if merged.get("mental_weather"):
        return True
    if merged.get("gratitudes"):
        return True
    return False


def format_extraction(merged: dict[str, Any]) -> str:
    """Format a merged Scribe payload as a readable plain-text block. Voice-neutral.
    Returns an empty string if there's nothing to show; callers decide what to do.
    """
    if not has_signal(merged):
        return ""

    lines: list[str] = []

    ms = merged.get("meat_suit_maintenance", {})
    body_lines = [
        line for line in (
            _format_status_line("hydration", ms.get("hydration") or {}),
            _format_status_line("hunger", ms.get("hunger") or {}),
            _format_status_line("bathroom", ms.get("bathroom") or {}),
        )
        if line
    ]
    if body_lines:
        lines.append("body:")
        lines.extend(body_lines)

    sl = merged.get("spoon_ledger", {})
    spoons_lines = [
        line for line in (
            _format_status_line("work drain", sl.get("work_drain") or {}),
            _format_status_line("social battery", sl.get("social_battery") or {}),
            _format_status_line("executive function", sl.get("executive_function") or {}),
        )
        if line
    ]
    if spoons_lines:
        if lines:
            lines.append("")
        lines.append("spoons:")
        lines.extend(spoons_lines)

    weather = merged.get("mental_weather")
    if weather:
        if lines:
            lines.append("")
        lines.append("mental weather:")
        lines.append(f"  · {weather}")

    grats = merged.get("gratitudes") or []
    if grats:
        if lines:
            lines.append("")
        lines.append("gratitudes:")
        for g in grats:
            lines.append(f"  · {g}")

    return "\n".join(lines)
