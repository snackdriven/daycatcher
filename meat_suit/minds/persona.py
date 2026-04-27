"""Mind 1: The Persona. Front-end banter only. No journal awareness."""
from __future__ import annotations

from openai import AsyncOpenAI

from ..personas.catalog import get_persona


def _voice_note(voice_samples: list[str] | None) -> str:
    """Build a tiny appendix telling the persona how the user actually writes.

    The persona stays in-character; this just nudges cadence/case/punctuation
    so replies feel like they're sitting in the same chat thread the user is.
    Empty samples => empty note (no system noise).
    """
    if not voice_samples:
        return ""
    joined = "\n".join(f"- {s}" for s in voice_samples[:8])
    return (
        "\n\nQuiet style note (do not mention, do not parody): the user tends "
        "to write like this:\n"
        f"{joined}\n"
        "Match their general cadence, capitalization, and punctuation habits. "
        "Stay fully in your own character. Don't ape their slang back at them."
    )


def _context_note(time_note: str | None, message_note: str | None) -> str:
    """Append a small situational appendix to the system prompt.

    Both notes are optional. Empty/None values produce no system noise.
    """
    chunks = [c for c in (time_note, message_note) if c]
    if not chunks:
        return ""
    body = " ".join(chunks)
    return (
        "\n\nSituational note (do not mention, do not announce): "
        + body
    )


async def reply(
    client: AsyncOpenAI,
    *,
    model: str,
    vibe_key: str,
    history: list[dict],   # [{"role": "user"|"assistant", "content": "..."}]
    user_message: str,
    voice_samples: list[str] | None = None,
    time_ctx_note: str | None = None,
    message_ctx_note: str | None = None,
) -> str:
    persona = get_persona(vibe_key)
    system_content = (
        persona.system_prompt
        + _voice_note(voice_samples)
        + _context_note(time_ctx_note, message_ctx_note)
    )
    messages = [
        {"role": "system", "content": system_content},
        *history,
        {"role": "user", "content": user_message},
    ]
    resp = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.9,
        max_tokens=400,
    )
    return (resp.choices[0].message.content or "").strip()


async def react_to_rabbit_hole(
    client: AsyncOpenAI,
    *,
    model: str,
    vibe_key: str,
    raw_text: str,
    topic: str | None = None,
) -> str:
    """Persona reacts to a pasted link / weird-fact dump.

    If `topic` is provided (e.g. 'marine chronometers'), the persona names it
    naturally instead of giving a generic 'cool fact' acknowledgement.
    """
    persona = get_persona(vibe_key)
    topic_hint = (
        f"\nThe subject is roughly: {topic}. Mention it by name, casually, like "
        "a friend who just glanced at the link.\n"
        if topic and topic != "miscellaneous curiosity"
        else ""
    )
    msg = (
        f"The user just dropped this into the chat:\n{raw_text[:500]}\n"
        f"{topic_hint}\n"
        "React in-character in 1-2 short sentences. Treat their curiosity as "
        "legitimate, not as a task to triage. "
        "Do NOT say 'cool', 'interesting', 'noted', 'logged', 'filed', "
        "'acknowledged', or 'thanks for sharing'. "
        "Do NOT summarize the link or explain what it's about back to them. "
        "Just respond like you saw the same thing they did."
    )
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": persona.system_prompt},
            {"role": "user", "content": msg},
        ],
        temperature=0.9,
        max_tokens=150,
    )
    return (resp.choices[0].message.content or "").strip()
