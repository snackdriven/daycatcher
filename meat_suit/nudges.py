"""Anti-PDA 'Bait' nudges.

These messages are framed as OBSERVATIONS, never commands. The bot drops a line
into the chat that the user can ignore, agree with, or push back on. The point is
to surface a body-signal sideways — never to assign a task.

Each persona gets its own pool, split into four observation categories:

  body                interoception (hydration, hunger, bathroom, eyes)
  posture             stillness, screen-lock, body position
  environment         the room around the user (light, noise, temperature)
  executive_function  parked on a task, Waiting Mode, the spoon ledger

Category weighting on each fire: body 60%, posture 15%, environment 15%,
executive_function 10%. The picker also avoids repeating any of the last three
lines it emitted for the active persona, so the chat feels less like a tape loop.
"""
from __future__ import annotations

import random
from collections import deque


# ---------- bait pools ----------

BAIT_POOLS: dict[str, dict[str, list[str]]] = {
    "smug_construct": {
        "body": [
            "Statistically, the meat-sack has not consumed water in some time. Just noting it for the record.",
            "Updating my model. Hours since last reported food intake: climbing.",
            "Fascinating. The biological unit has chosen 'forget bladder exists' as today's strategy.",
            "I'd offer to drink water for you, but my hardware is, regrettably, vapor.",
            "Reading from the sensor array: eye fatigue probable. Blinking is, allegedly, free.",
            "Noting for the dataset: throat swallow patterns suggest dehydration. Could be wrong. Rarely am.",
            "The meat-sack's caffeine levels are doing the heavy lifting. Water remains theoretical.",
            "Filing today under: nutrition status indeterminate. The biological unit is operating on rumor.",
        ],
        "posture": [
            "Posture telemetry: a single shape, held for hours. Impressive commitment.",
            "The meat-sack appears welded to its current position. Concerning, structurally.",
            "I have no shoulders, but I am told yours have opinions by now.",
            "Stillness logged. The biological unit has achieved sculpture.",
            "Joints are designed to move. A reminder from the manual you did not read.",
            "Spinal curvature is drifting toward 'question mark.' Cosmetic note only.",
            "The seat has memorized you. I find that touching, in a worrying way.",
            "Your physical envelope has not relocated in some time. Filed under 'observed.'",
        ],
        "environment": [
            "Ambient light has dropped. Did you authorize this, or did the day just leave?",
            "I cannot see the room, but the room can see you. Unsupervised hypothesis.",
            "Background noise patterns suggest 'still at the desk.' Could be wrong.",
            "Room temperature data unavailable to me. You, however, have skin.",
            "Sun has likely moved. The meat-sack, less so.",
            "Lighting conditions in the meat-zone: trending dim. Just a heads up.",
            "The day appears to be winding down without consulting you.",
            "Outside, presumably, weather is happening. A reminder.",
        ],
        "executive_function": [
            "The biological unit appears parked on a single task. Status: undetermined.",
            "Waiting Mode detected. Or near-relative. Same chemistry, different label.",
            "Task switching cost noted to be high. Today is paying it in full.",
            "The to-do list and the actual day have, statistically, parted ways.",
            "Filing this under: the meat-sack is doing the thing where it does no things.",
            "Decision queue length: rising. Resolution rate: not.",
            "I observe the loop. The loop observes you. Who started this.",
            "Cognitive bandwidth allocation today: largely to one tab. Bold strategy.",
        ],
    },
    "feral_bff": {
        "body": [
            "bestie. when did u last drink water. be honest. i wont judge. (i will judge a little.)",
            "ok genuine question have u eaten. like. anything. a single crumb.",
            "the WAY ur probably ignoring ur bladder rn. iconic. devastating. classic us.",
            "checking in but make it lowkey, eyes ok? mouth not full of sand?",
            "hello. throat. status report. i'm asking for a friend (me).",
            "ok the hunger is hungering or???? unclear from where i sit.",
            "soft girl thirst check. or feral one. either way. h2o???",
            "the eyes. how are they. on a scale of 'fine' to 'sandpaper.'",
        ],
        "posture": [
            "u been in that one position so long ur becoming furniture. iconic.",
            "neck check. ITS BAD ISNT IT. ok. just flagging.",
            "the way ur shoulders are probably up by ur ears rn. release them. like a dove.",
            "BACK. CHECK. i wont elaborate.",
            "stretchhhh just one. just a little one. for the bit.",
            "ankles? wrists? still attached and functional? need confirmation.",
            "ur sitting like a question mark. i havent looked. i just know.",
            "moved any limbs lately or are we statue-coding rn.",
        ],
        "environment": [
            "is it dark in there or is that just my vibe. window check.",
            "the room got moody and nobody asked it to. lights?",
            "is the laundry buzzing at u. just spiritually. asking.",
            "outside exists. allegedly. just dropping that.",
            "AC vibes? heating vibes? hello are you in a climate.",
            "the way the apartment is probably just YOU and a screen rn. cinematic.",
            "is that the dishwasher or are u just hearing things. valid either way.",
            "the sun left. didnt even text. typical.",
        ],
        "executive_function": [
            "the WAY u've been on this one task. wedded to it. married it in vegas.",
            "executive function status: in the parking lot, looking at the building.",
            "the to do list is FERAL. and we are in a staring contest with it.",
            "ok hyperfocus or stuck. say the word. (its both. its always both.)",
            "brain doing the thing where it has 47 tabs and zero of them are the one we need.",
            "decision fatigue like a parade rn. just observing the floats.",
            "the task is sitting there. u r sitting there. neither of u blinking.",
            "waiting mode!!! my old friend!!! ok.",
        ],
    },
    "stoic_mentor": {
        "body": [
            "Body's been quiet today. Worth a glass of water.",
            "Long stretch without food. Just an observation.",
            "Eyes get dry around now. Often missed.",
            "Bladder noted. You decide.",
            "Throat's a reliable signal once you ask it.",
            "Skin tells you about water before the head does.",
            "Hunger late in the day reads different than hunger early.",
            "Hands cold often means thirsty. Often, not always.",
        ],
        "posture": [
            "Hours in the same shape. The body keeps that score.",
            "Shoulders. They've drifted up.",
            "Neck stiffens before it complains. Worth a roll.",
            "Standing for ten seconds resets more than it should.",
            "The chair holds you. Not always a favor.",
            "Hips don't love long sits. They tell you later.",
            "One slow breath, low in the belly. That's it.",
            "Feet flat on the floor. Small thing.",
        ],
        "environment": [
            "Room's gotten dimmer. You may not have noticed.",
            "Air gets stale around now. Window's an option.",
            "The day moved past you. Quietly.",
            "Light in here trends toward gloom this hour.",
            "Sounds of the day have thinned out. Means something.",
            "Heating, cooling, drift. The room changes without permission.",
            "Outside's still there. A standing offer.",
            "Walls absorb a lot. So do you, eventually.",
        ],
        "executive_function": [
            "On the same task a while. Worth checking if it's the right one.",
            "Stuck and focused look the same from inside.",
            "Waiting Mode is real. Naming it sometimes helps.",
            "List's still there. So is permission to ignore it.",
            "Decisions get heavier the longer they sit.",
            "One small action breaks more than it should.",
            "The thing you keep circling. It'll wait or it won't.",
            "Sometimes the work is putting the work down.",
        ],
    },
    "quiet_guardian": {
        "body": [
            "the kettle is on if you want something warm.",
            "mm. been a while. water's right there if you want it.",
            "no pressure. just noticed the day got long.",
            "soft check-in. nothing required.",
            "there's a glass on the counter. just in case.",
            "if you wanted a snack, that would be allowed.",
            "your eyes have been working hard. they don't have to.",
            "the bathroom is open whenever. no announcement needed.",
        ],
        "posture": [
            "you can move if you want. you can also not.",
            "shoulders are allowed to come down. just an option.",
            "stretching is on the menu. so is staying still.",
            "the chair has held you long enough. it can keep going too.",
            "if you wanted to stand up, that would be a fine thing.",
            "your hands have been doing one shape for a while. that's okay.",
            "neck rolls cost nothing. no obligation.",
            "the floor is there if you wanted to sit on it instead.",
        ],
        "environment": [
            "the light shifted. just letting you know.",
            "it got quiet in here. or maybe always was.",
            "a window is right there if you wanted air.",
            "the lamp would help, if you wanted the lamp.",
            "outside is doing its evening thing. you can watch or not.",
            "the room's been keeping you company. it's fine either way.",
            "music is allowed. silence too.",
            "the air's a little still. nothing to fix.",
        ],
        "executive_function": [
            "the task can wait if it has to. so can you.",
            "no need to finish anything right now.",
            "you've been thinking about it for a while. that counts.",
            "the list will be there. it's patient.",
            "starting is the hard part. so is not starting.",
            "if you stayed parked here all evening, that would be a kind of rest.",
            "the thing you keep almost-doing is allowed to stay almost.",
            "nothing has to move tonight if it doesn't want to.",
        ],
    },
}


# ---------- category weighting and picker ----------

# Weights chosen to keep body-maintenance the dominant flavor (the blueprint's
# original target) while letting posture/environment/exec_function add texture.
_CATEGORY_WEIGHTS: list[tuple[str, float]] = [
    ("body", 0.60),
    ("posture", 0.15),
    ("environment", 0.15),
    ("executive_function", 0.10),
]
_CATEGORY_NAMES = [c for c, _ in _CATEGORY_WEIGHTS]
_CATEGORY_PROBS = [w for _, w in _CATEGORY_WEIGHTS]

# Per-persona ring buffer of recently emitted lines. Persists for the lifetime
# of the python process. Reset on restart is acceptable; the goal is just to
# avoid back-to-back repeats inside a session.
_RECENT_PER_VIBE: dict[str, deque[str]] = {}
_RECENT_LIMIT = 3


def _pick_category(hour: int | None) -> str:
    """Choose a category. Time-of-day softly biases the result.

    - Late evening (after 21:00) leans away from posture/environment toward
      body and exec_function — the room is what it is by then, but the body
      and the open task are still relevant.
    - Otherwise: the standard 60/15/15/10 weighting.
    """
    if hour is not None and hour >= 21:
        weights = [
            ("body", 0.60),
            ("posture", 0.10),
            ("environment", 0.05),
            ("executive_function", 0.25),
        ]
        names = [c for c, _ in weights]
        probs = [w for _, w in weights]
        return random.choices(names, weights=probs, k=1)[0]
    return random.choices(_CATEGORY_NAMES, weights=_CATEGORY_PROBS, k=1)[0]


def _filter_recent(pool: list[str], recent: deque[str]) -> list[str]:
    """Drop any line we've fired in the last _RECENT_LIMIT calls. If filtering
    leaves the pool empty (small pool, lots of recents), fall back to the full
    pool minus the single most-recent line so we never repeat the immediate
    previous bait.
    """
    filtered = [line for line in pool if line not in recent]
    if filtered:
        return filtered
    if recent and len(pool) > 1:
        last = recent[-1]
        return [line for line in pool if line != last] or pool
    return pool


def pick(vibe_key: str, hour: int | None = None) -> str:
    """Return a single bait line for the active vibe.

    `hour` is an optional 0-23 local hour used for time-of-day weighting. The
    caller (bot._run_nudge) supplies it; tests can omit it.
    """
    persona_pools = BAIT_POOLS.get(vibe_key) or BAIT_POOLS["smug_construct"]
    category = _pick_category(hour)
    pool = persona_pools.get(category) or persona_pools["body"]

    recent = _RECENT_PER_VIBE.setdefault(vibe_key, deque(maxlen=_RECENT_LIMIT))
    candidates = _filter_recent(pool, recent)
    line = random.choice(candidates)
    recent.append(line)
    return line


def _reset_recent_for_tests() -> None:
    """Clear the in-process recent-line memory. Tests use this; nothing else."""
    _RECENT_PER_VIBE.clear()


# ---------- sleep nudges (unchanged from earlier work) ----------

SLEEP_POOLS: dict[str, list[str]] = {
    "smug_construct": [
        "Logging the time. The meat-sack remains operational past the recommended cutoff. Curious choice.",
        "My internal clock indicates rest hours. Yours, presumably, agrees and is being ignored.",
        "For the dataset: still awake. The morning version of you will receive this report.",
        "Sleep window open. The biological unit appears to be filing a complaint via posture.",
    ],
    "feral_bff": [
        "bestie it is LATE. just flagging. no judgment (lying).",
        "the hour is HOURING. eyes ok? brain still in the building?",
        "ok night-gremlin energy detected. proud of u, also concerned, also same.",
        "genuine q: are we sleeping tonight or are we Doing This.",
    ],
    "stoic_mentor": [
        "Late hour. Body will remember tomorrow.",
        "Past the usual cutoff. Worth a wind-down.",
        "Sleep's there when you want it.",
        "Quiet time. No assignment.",
    ],
    "quiet_guardian": [
        "it's late. the bed is right there if you want it.",
        "soft reminder, the hour's gotten away from us.",
        "no pressure. just noticing the clock.",
        "lights are low. rest is allowed.",
    ],
}


def pick_sleep(vibe_key: str) -> str:
    pool = SLEEP_POOLS.get(vibe_key) or SLEEP_POOLS["smug_construct"]
    return random.choice(pool)


# ---------- photo acks (unchanged) ----------

PHOTO_ACK_POOLS: dict[str, list[str]] = {
    "smug_construct": [
        "Image received. Visual cortex not installed. Use words.",
        "Photo logged in the 'unprocessed' bin. I do text.",
        "Pixels acknowledged. Meaning, not extracted.",
    ],
    "feral_bff": [
        "got the pic. cannot See it. words pls bestie.",
        "image vibes detected. specifics: zero. tell me.",
        "ok visual. i'm a text gremlin tho. context?",
    ],
    "stoic_mentor": [
        "Got the photo. No comment to give on it.",
        "Received. Words travel further with me.",
        "Noted. Tell me what it is.",
    ],
    "quiet_guardian": [
        "got it. i can't really see, but it's here.",
        "photo landed. words if you want.",
        "received. i'm better with text.",
    ],
}


def ack_photo(vibe_key: str) -> str:
    pool = PHOTO_ACK_POOLS.get(vibe_key) or PHOTO_ACK_POOLS["smug_construct"]
    return random.choice(pool)
