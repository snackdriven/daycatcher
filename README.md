# Project Meat-Suit

You are going to text a smug AI for two weeks and at the end of it your phone will hand you a journal you didn't write, in a voice that sounds suspiciously like yours, describing a week you only half remember living.

That's the whole pitch.

It's a Telegram bot. You banter with a persona. A second mind quietly extracts signal from the chat exhaust. A third mind runs at 11:59 PM and ghostwrites the day. None of them know the others exist.

It's for anyone who has tried to keep a journal and bounced off it because the act of being asked to journal makes you stop wanting to.

There's also a [browser companion](./web/) if you want to try the chat side without the journal side.

---

## The three minds

| | Mind | Job | Sees |
|---|---|---|---|
| 1 | **Persona** | Banters with you in Telegram | the last 20 messages, nothing else |
| 2 | **Scribe** | Extracts JSON from chat exhaust | raw transcripts only |
| 3 | **Weaver** | Writes the daily journal at 23:59 | the day's structured bundle |

The Persona has zero awareness of the journal. That is the entire architectural point. You stay relaxed because you're not writing for posterity. You're just talking and tapping. The thing that writes things down has never met you and never will.

---

## What you can actually do with it

**Tap.** A persistent keyboard sits under the text input with four buttons: `Overwhelmed`, `Non-Verbal`, `Safe Food Only`, `Brain Fog`. One tap logs a status event and gets one in-character reply. Smug Construct will say *"Logged: meat-sack at capacity. Reducing chatter overhead."* Quiet Guardian will say *"mm. i'll be quiet. nothing required."* No follow-up question. No "tell me more." That is the whole interaction.

If you send a photo, the bot drops a short in-vibe ack and moves on. No vision pass, no storage, nothing piped to the journal. Use words.

**Paste.** Drop a URL or open a message with `fun fact` or `til` and it gets logged as a curiosity event. The Weaver treats these as legitimate micro-gratitudes worth writing about. *"I spent some time looking into 18th-century clockmaking. It was a nice break from the office noise."*

**`/check`.** A six-option poll, on demand only.

> CURRENT MEAT-SUIT STATUS:
> ◯ Hydrated (Liar)
> ◯ Resembling a Raisin
> ◯ I have forgotten I have a mouth
> ◯ Busy (Don't perceive me)
> ◯ Eyes are dry
> ◯ Bladder is a problem I'm ignoring

This used to fire every day at 2 PM. It doesn't anymore. A recurring six-option questionnaire is exactly the kind of intrusion this project is built to avoid.

**`/week`.** Sunday at 10 PM, the weekly Weaver reads up to seven daily journals plus the structured signals and notices things you can't see from inside the week. Repeats. Adjacencies. Absences. Soft trajectories. *"Tuesday and Wednesday both ended with the kettle line. Thursday I forgot food until 8."* No advice. No charts. If the week was thin, it says so and stops.

---

## The four vibes

Switch between them with `/vibe`.

- `smug_construct` — dry, condescending AI. Spite-based self-care.
- `feral_bff` — chaotic-good lowercase commiseration.
- `stoic_mentor` — short, grounded, structural.
- `quiet_guardian` — soft, lowercase, parallel-play.

Each persona has its own voice on every interaction, including the button-tap acknowledgements. The Weaver also reads recent samples of how *you* actually write and mirrors your cadence when it ghostwrites the journal, so the entries don't feel like they came from a content farm.

---

## Setup

```bash
cd meat_suit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_ID, OPENAI_API_KEY
python run.py
```

Three secrets:

1. **TELEGRAM_BOT_TOKEN** from `@BotFather` → `/newbot` → copy the token.
2. **TELEGRAM_OWNER_ID** from `@userinfobot`. This locks the bot to only you. Anyone else who finds it is silently ignored.
3. **OPENAI_API_KEY** from <https://platform.openai.com/api-keys>.

---

## Commands

- `/start` — show vibe, list commands, bring the keyboard up
- `/vibe` — list vibes, or `/vibe feral_bff` to switch
- `/menu off|on` — hide or show the low-spoon keyboard
- `/journal` — show the most recent daily entry
- `/week` — generate or re-show the weekly look-back
- `/check` — fire the body-check poll on demand
- `/weave_now` — run the Scribe and Weaver on today's chat (useful for testing)
- `/data` — show the Scribe's structured extraction (today, `yesterday`, or `week`)
- `/sleep` — view or change sleep-nudge settings (`on` / `off` / `off forever`)

## Schedule (in your timezone)

- Every 30 min — Scribe pass on new chat exhaust
- 11:00 — text nudge
- 17:30 — text nudge
- 23:59 — Weaver writes and delivers the daily entry
- 22:00 Sunday — weekly Weaver delivers the pattern paragraph

---

## Time awareness

The Persona gets a small time note on every reply: what day it is, what part of the day it is, whether it's a weekend evening, and whether you're past your sleep cutoff. It also gets told when you've been gone for several days, with explicit instructions not to greet you or ask where you went.

A nightly job watches the clock. If you're still messaging past your weeknight cutoff (default 23:00) or weekend cutoff (default 01:00), the bot drops a single in-character sleep observation that you can ignore. It will not nag twice the same night.

- `/sleep` — show current settings
- `/sleep off` — snooze tonight only
- `/sleep off forever` — disable sleep nudges entirely
- `/sleep on` — re-enable

The Weaver also gets two small extras: a short rhythm summary of when the day's chat clustered (mostly evening, past midnight, big quiet gap), and the dates of any late-night sessions inside the week it's writing about. It uses these as texture, not as judgment.

---

## Privacy

Everything lives in `./data/meatsuit.db`. Local SQLite, eight tables, no cloud anything.

The bot only responds to your `TELEGRAM_OWNER_ID`. Anyone else is silently ignored, including in groups.

The Persona only ever sees the rolling 20-message chat window. It never reads journals, signals, or polls. That part is on purpose.

---

## Browser companion

`/web` is a browser version of just **Mind 1**. Landing page plus an interactive demo where you can switch between the four vibes and chat. Sessions are stored server-side per persona. The Scribe and Weaver only live in the python version.

```bash
cd web && npm install && npm run dev
```

Stack: Express + Vite + React + Tailwind + SQLite + OpenAI Responses API.

---

## File map

```
meat_suit/
├── run.py
├── requirements.txt
├── .env.example
├── meat_suit/             # the python bot
│   ├── config.py          # env loader
│   ├── db.py              # sqlite, 8 tables
│   ├── nudges.py          # bait pools per persona
│   ├── low_spoon.py       # button menu + per-persona acks
│   ├── body_check.py      # /check poll definition
│   ├── bot.py             # telegram handlers, jobqueue
│   ├── personas/
│   │   └── catalog.py     # 4 personas as system prompts
│   └── minds/
│       ├── persona.py     # mind 1, chat + rabbit-hole-react
│       ├── scribe.py      # mind 2, json extraction
│       └── weaver.py      # mind 3, multi-source midnight ghostwriter
└── web/                   # browser companion (mind 1 only)
    ├── client/            # react frontend
    ├── server/            # express + sqlite + persona prompts
    └── shared/            # zod schema
```

---

## License

MIT. Built slow. Built quiet.
