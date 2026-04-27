"""2 PM Body Check — Telegram poll, anonymous, multi-answer.

Tapping options is a micro-interaction: feels like nothing, gives the Scribe
clean structured data. The poll question/options stay constant for predictability
(pattern-recognition is friendly to AuDHD); only the Weaver interprets them.
"""
from __future__ import annotations

POLL_QUESTION = "CURRENT MEAT-SUIT STATUS:"

POLL_OPTIONS: list[str] = [
    "Hydrated (Liar)",
    "Resembling a Raisin",
    "I have forgotten I have a mouth",
    "Busy (Don't perceive me)",
    "Eyes are dry",
    "Bladder is a problem I'm ignoring",
]

# Map option text → semantic signal key for the Scribe/Weaver
OPTION_SIGNALS: dict[str, str] = {
    "Hydrated (Liar)":                      "hydration_claimed_doubtful",
    "Resembling a Raisin":                  "hydration_low",
    "I have forgotten I have a mouth":      "hunger_dissociated",
    "Busy (Don't perceive me)":             "social_unavailable",
    "Eyes are dry":                         "hydration_low_eyes",
    "Bladder is a problem I'm ignoring":    "bathroom_avoiding",
}
