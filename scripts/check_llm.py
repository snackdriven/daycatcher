"""One-shot sanity check: prove the configured LLM endpoint actually talks back.

Run with: cd meat_suit && python scripts/check_llm.py

This is intentionally minimal. If it prints "OK", Persona/Scribe/Weaver can talk
to your provider. If it errors, the bot would have errored on first message anyway.
"""
from __future__ import annotations

import asyncio
import sys

from openai import AsyncOpenAI

from meat_suit.config import load_config


async def main() -> int:
    cfg = load_config()
    client = AsyncOpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url)

    print(f"endpoint: {cfg.llm_base_url}")
    print(f"model:    {cfg.mind1_model}")
    print()

    # 1) Plain chat
    resp = await client.chat.completions.create(
        model=cfg.mind1_model,
        messages=[
            {"role": "system", "content": "Reply in exactly one short sentence."},
            {"role": "user", "content": "Say hi."},
        ],
    )
    text = resp.choices[0].message.content
    print(f"chat: {text!r}")

    # 2) JSON mode (Scribe relies on this)
    resp = await client.chat.completions.create(
        model=cfg.mind2_model,
        messages=[
            {"role": "system", "content": "Reply with a JSON object only."},
            {
                "role": "user",
                "content": 'Return JSON: {"status": "ok", "answer": 42}',
            },
        ],
        response_format={"type": "json_object"},
    )
    json_text = resp.choices[0].message.content
    print(f"json: {json_text!r}")

    print()
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
