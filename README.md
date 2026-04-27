# Project Meat-Suit (v2.1 — Visual, Haptic, and Voice-Mirrored)

A Zero-Demand Decompression Chamber. Telegram chat in, ghostwritten journal out.

Multi-agent journaling + bio-maintenance for AuDHD / PDA / alexithymia. You banter with a persona; a silent Scribe extracts interoception signals from the chat exhaust; a Midnight Weaver turns the day's signals into a first-person journal entry — automatically, every night at 11:59 PM.

**v2 reclaims the cognitive space we saved by skipping voice notes.** Instead of typing-or-talking, you can now also just *tap*, *snap*, or *paste*.

**v2.1 makes it sound like you.** The Weaver reads recent samples of how you actually write and mirrors your cadence, capitalization, and word choice when it ghostwrites the journal. Personas pick up the same hint and match the chat thread you're already in. All four personas plus the Weaver also got a 2026 anti-AI-tell pass: no em-dashes, no rule of three, no negative parallelism, no AI vocab list, no reflexive validation.

## Architecture (Quad-Mind, isolated)

| Mind | Role | Trigger | Input | Knows about... |
|---|---|---|---|---|
| **1. Persona** | Banter front-end via Telegram | every text/photo/link | chat + neutral observations | only the current chat |
| **2. Scribe** | Extracts JSON from chat exhaust | every 30 min + before Weaver | raw transcripts | only raw transcripts |
| **3. Weaver** | Writes the daily journal entry | 23:59 local time | merged JSON bundle | only the bundle |
| **4. Eye** | Photo-to-data | every photo | image bytes | one photo at a time |

The Persona has zero awareness of the journal — that's the point. You stay unmasked because you're not "writing for posterity," you're just talking, tapping, and snapping.

## v2 Features

### 🔘 Low-Spoon Button Menu
A persistent Telegram keyboard sits under the text input:
```
[ 🚨 Overwhelmed ]   [ 😶 Non-Verbal ]
[ 🍕 Safe Food Only ] [ 🫧 Brain Fog ]
```
One tap = one logged status event + one in-character acknowledgement. No "why?" follow-up. Each persona has its own ack pool — Smug Construct will say *"Logged: meat-sack at capacity. Reducing chatter overhead."*; Quiet Guardian will say *"mm. i'll be quiet. nothing required."*

Toggle: `/menu off` to hide, `/menu on` to bring back.

### 📷 Photo-to-Data (Mind 4: The Eye)
Snap a photo of anything — desk, food, sky, cat. The Eye produces a neutral observation and structured signals (clutter level, food state, caffeine-vessels-visible, executive-function proxy). The Persona reacts to the *observation* in-character (it never sees the structured data — strict isolation). The Scribe gets the signals; the Weaver weaves them into the night's entry as concrete sensory detail.

### 🐇 Rabbit Hole Repository
Paste a URL or start a message with "fun fact / til / did you know" and it's logged as a curiosity-event. The Weaver treats these as legitimate micro-gratitudes — *"I spent some time looking into 18th-century clockmaking; it was a nice break from the office noise."*

### 📊 Body Check Poll (`/check`)
A multi-answer Telegram poll, on demand:

> CURRENT MEAT-SUIT STATUS:
> ◯ Hydrated (Liar)
> ◯ Resembling a Raisin
> ◯ I have forgotten I have a mouth
> ◯ Busy (Don't perceive me)
> ◯ Eyes are dry
> ◯ Bladder is a problem I'm ignoring

Used to fire daily at 2 PM. It doesn't anymore. A recurring six-option questionnaire is exactly the kind of intrusion this project is supposed to avoid, so the poll is now opt-in via `/check` when *you* feel like giving the system a structured ping.

### 🪡 Weekly Look-Back (`/week`, Sunday 10 PM)
The daily Weaver tells you what happened that day. The weekly Weaver tells you what your week *was*, in one short paragraph in your voice. It reads up to seven daily journals plus the structured signals and notices things you can't see from inside the week: repeats, adjacencies, absences, soft trajectories. No advice. No "here's a chart." Just *"Tuesday and Wednesday both ended with the kettle line. Thursday I forgot food until 8."* If the week was thin, it says so and stops.

## Personas (`/vibe`)
- `smug_construct` — dry, condescending AI. Spite-based self-care.
- `feral_bff` — chaotic-good lowercase commiseration.
- `stoic_mentor` — short, grounded, structural.
- `quiet_guardian` — soft, lowercase, parallel-play.

## Setup

```bash
cd meat_suit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID, OPENAI_API_KEY
python run.py
```

### Get the three secrets
1. **TELEGRAM_BOT_TOKEN** — message `@BotFather` → `/newbot` → copy token.
2. **TELEGRAM_OWNER_ID** — message `@userinfobot` → returns your numeric ID. Locks the bot to ONLY you.
3. **OPENAI_API_KEY** — from <https://platform.openai.com/api-keys>.

## Commands
- `/start` — show vibe + commands, bring the keyboard up
- `/menu off|on` — hide/show the low-spoon keyboard
- `/vibe` — list vibes, or `/vibe feral_bff` to switch
- `/journal` — show the most recent daily journal entry
- `/week` — generate (or re-show) the weekly look-back
- `/check` — fire the body-check poll on demand
- `/weave_now` — run Scribe + Weaver on today's chat (great for testing)

## Schedule (in your timezone)
- **Every 30 min** — Scribe pass on new chat exhaust
- **11:00** — text nudge (in-character observation)
- **17:30** — text nudge
- **23:59 daily** — Weaver writes & delivers the journal entry
- **22:00 Sunday** — weekly Weaver delivers the pattern-noticing paragraph

## Privacy
- Everything in `./data/meatsuit.db` (local SQLite).
- Bot only responds to your `TELEGRAM_OWNER_ID`. Anyone else is silently ignored.
- Persona only ever sees the rolling 20-message chat window — never journals, signals, photos, or polls.
- Photos are processed through OpenAI vision; raw image bytes are NOT stored locally (only Telegram's `file_id` for re-fetch + the neutral observation + structured signals).

## File map
```
meat_suit/
├── run.py
├── requirements.txt
├── .env.example
└── meat_suit/
    ├── config.py          # env loader
    ├── db.py              # SQLite (8 tables)
    ├── nudges.py          # bait pools per persona
    ├── low_spoon.py       # button menu + per-persona acks
    ├── body_check.py      # 2 PM poll definition
    ├── bot.py             # Telegram handlers, JobQueue, vision pipeline
    ├── personas/
    │   └── catalog.py     # 4 personas as system prompts
    └── minds/
        ├── persona.py     # Mind 1 — chat + image-react + rabbit-hole-react
        ├── scribe.py      # Mind 2 — JSON extraction
        ├── weaver.py      # Mind 3 — multi-source midnight ghostwriter
        └── eye.py         # Mind 4 — photo-to-data
```
