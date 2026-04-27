"""Anti-PDA 'Bait' nudges.

These messages are framed as OBSERVATIONS, never commands. The bot drops a line
into the chat that the user can ignore, agree with, or push back on. The point is
to surface a body-signal sideways — never to assign a task.

Each persona gets its own bait pool so the nudge sounds in-character.
"""
from __future__ import annotations

import random


BAIT_POOLS: dict[str, list[str]] = {
    "smug_construct": [
        "Statistically, the meat-sack has not consumed water in some time. Just noting it for the record.",
        "Updating my model. Hours since last reported food intake: climbing.",
        "Fascinating. The biological unit has chosen 'forget bladder exists' as today's strategy.",
        "I'd offer to drink water for you, but my hardware is, regrettably, vapor.",
    ],
    "feral_bff": [
        "bestie. when did u last drink water. be honest. i wont judge. (i will judge a little.)",
        "ok genuine question have u eaten. like. anything. a single crumb.",
        "the WAY ur probably ignoring ur bladder rn. iconic. devastating. classic us.",
        "checking in but make it lowkey, eyes ok? mouth not full of sand?",
    ],
    "stoic_mentor": [
        "Body's been quiet today. Worth a glass of water.",
        "Long stretch without food. Just an observation.",
        "Eyes get dry around now. Often missed.",
        "Bladder noted. You decide.",
    ],
    "quiet_guardian": [
        "the kettle is on if you want something warm.",
        "mm. been a while. water's right there if you want it.",
        "no pressure. just noticed the day got long.",
        "soft check-in. nothing required.",
    ],
}


def pick(vibe_key: str) -> str:
    pool = BAIT_POOLS.get(vibe_key) or BAIT_POOLS["smug_construct"]
    return random.choice(pool)
