"""Low-Spoon Button Menu — non-verbal shortcuts.

A persistent ReplyKeyboard sits under the text input. Tapping a button is a
one-tap status update: the bot acknowledges in the active vibe and the Scribe
sees it as a structured signal.
"""
from __future__ import annotations

import random


# Visible button label  →  internal state key
BUTTONS: dict[str, str] = {
    "🚨 Overwhelmed":       "overwhelmed",
    "😶 Non-Verbal":        "non_verbal",
    "🍕 Safe Food Only":    "safe_food_only",
    "🫧 Brain Fog":         "brain_fog",
}

# 2x2 keyboard layout
KEYBOARD_LAYOUT: list[list[str]] = [
    ["🚨 Overwhelmed", "😶 Non-Verbal"],
    ["🍕 Safe Food Only", "🫧 Brain Fog"],
]


def state_for_label(label: str) -> str | None:
    return BUTTONS.get(label.strip())


# Per-persona, per-state acknowledgement pools.
# Rules: NO 'logged', 'noted', 'acknowledged', 'copy that', 'understood', 'got it'.
# Sound like a friend reacting in real time, not a system confirming a state change.
# 1-2 short sentences. No follow-up questions. No commands.
ACKNOWLEDGEMENTS: dict[str, dict[str, list[str]]] = {
    "smug_construct": {
        "overwhelmed": [
            "Right. I'll lower my volume and judge the wallpaper for a while.",
            "Then I'll stop talking at you. The wallpaper and I have unfinished business anyway.",
            "Fine, dimming the chatter. The hardware is doing its thing; my commentary can wait.",
        ],
        "non_verbal": [
            "Words can take a number. I'll be over here, smug and silent.",
            "Then I'll save my hot takes for cold storage.",
            "Fine. I'll narrate the room to myself.",
        ],
        "safe_food_only": [
            "Beige and reliable. The classics for a reason.",
            "The palette has shrunk to crackers and dignity. Respect.",
            "A safe-food day. Sensible meat-sack behavior, frankly.",
        ],
        "brain_fog": [
            "The wetware is buffering. I'll keep my sentences short and my judgments shorter.",
            "Right. Lower-resolution day. I'll match the bitrate.",
            "Fog. Fine. I'll be less.",
        ],
    },
    "feral_bff": {
        "overwhelmed": [
            "shhhhh ok. dim lights mode. im just here being a houseplant.",
            "say less bestie. ill be a quiet little gremlin in the corner.",
            "OKAY no input no output. im a vibe in airplane mode.",
        ],
        "non_verbal": [
            "mute it is. im an ambient lifeform now.",
            "no words. dont perceive me. perfect.",
            "shhhh ok. im just a feeling in the room.",
        ],
        "safe_food_only": [
            "beige food summit. crackers are valid. cheese-shaped objects are valid.",
            "safe food era. nothing has to taste like a personality today.",
            "all hail the same three foods. honestly aspirational.",
        ],
        "brain_fog": [
            "static-tv brain. ill type slower so we match.",
            "fog goggles on. small thoughts only. its fine.",
            "low-res day. me too tbh.",
        ],
    },
    "stoic_mentor": {
        "overwhelmed": [
            "Heavy day. I'll be less.",
            "Heard. Holding still.",
            "Loud world. I'll match it down.",
        ],
        "non_verbal": [
            "Words can wait.",
            "Quiet's fine.",
            "No talking. Good.",
        ],
        "safe_food_only": [
            "Familiar food. Sensible.",
            "Same plate. Works.",
            "Beige holds up.",
        ],
        "brain_fog": [
            "Slow day. Slow's allowed.",
            "Low signal. That's weather.",
            "Fog. We wait it out.",
        ],
    },
    "quiet_guardian": {
        "overwhelmed": [
            "mm. i'll be quiet. nothing's required.",
            "soft. i'll stay in the next room.",
            "that's a lot. i'll keep small.",
        ],
        "non_verbal": [
            "no words. that's okay.",
            "mm. silence suits the room.",
            "yeah. quiet's fine.",
        ],
        "safe_food_only": [
            "beige is okay. crackers count.",
            "safe is good. nothing to prove.",
            "the same things. that's allowed.",
        ],
        "brain_fog": [
            "foggy day. you don't have to be sharp.",
            "soft brain. that's allowed.",
            "slow's fine. nothing's waiting.",
        ],
    },
}


def acknowledge(vibe_key: str, state: str) -> str:
    pool = ACKNOWLEDGEMENTS.get(vibe_key, ACKNOWLEDGEMENTS["smug_construct"]).get(state, [])
    if not pool:
        return "noted."
    return random.choice(pool)
