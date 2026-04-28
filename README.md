# Project Meat-Suit

You are going to text a smug AI for two weeks and at the end of it the app will hand you a journal you didn't write, in a voice that sounds suspiciously like yours, describing a week you only half remember living.

That's the whole pitch.

It's a single-user web app. You banter with a persona in a browser tab. A second mind quietly extracts signal from the chat exhaust. A third mind runs at 11:59 PM and ghostwrites the day. None of them know the others exist.

It's for anyone who has tried to keep a journal and bounced off it because the act of being asked to journal makes you stop wanting to.

---

## The three minds

|     | Mind        | Job                                       | Sees                            |
| --- | ----------- | ----------------------------------------- | ------------------------------- |
| 1   | **Persona** | Banters with you in the chat tab          | the last 20 messages, nothing else |
| 2   | **Scribe**  | Extracts JSON from chat exhaust           | raw transcripts only            |
| 3   | **Weaver**  | Writes the daily journal at 23:59         | the day's structured bundle     |

The Persona has zero awareness of the journal. That is the entire architectural point. You stay relaxed because you're not writing for posterity. You're just talking. The thing that writes things down has never met you and never will.

---

## What you can actually do with it

**Chat.** Top-right of the chat page is a four-letter switcher: `C / F / A / I`. Tap one to swap voices mid-thread. The new voice picks up from where the last one left off. The `?` next to the switcher opens a card explaining who's who.

**Journal.** A second tab lists daily entries newest-first, with a small unread dot when the Weaver has shipped one you haven't opened. Sunday entries get a separate weekly look-back ribbon.

**Data.** A third tab is a quiet dashboard. Total messages, current streak, percent-after-11pm, vibe distribution, sleep adherence dots, hourly density, this-week-vs-last-week. No advice, no charts that beg you to do something. It's there so you can look at it and close the tab.

**Settings.** Default vibe, sleep windows (weeknight + weekend cutoff hours), nudge toggles, "snooze tonight" for the sleep nudge, full JSON export of everything, and a lock button that clears your session cookie.

**Check-in button.** If you tap it the active persona drops a single in-character status line into the chat. No follow-up question, no "tell me more." That is the whole interaction.

---

## The four vibes

Switch any time from the chat header. Each persona has its own voice on every interaction, including the nudges. The Weaver also reads recent samples of how *you* actually write and mirrors your cadence when it ghostwrites the journal, so the entries don't feel like they came from a content farm.

- `the CEO` (C) — calm, clipped, faintly amused. Directives over hand-holding.
- `the Feral BFF` (F) — loud, no caps, will roast you with love. Minimal punctuation.
- `the Almanac` (A) — spare, observational, steady cadence. Very few questions.
- `the Innkeeper` (I) — soft, attentive, low-volume kindness. Hospitality cues.

---

## Setup

```bash
git clone https://github.com/snackdriven/meat-suit.git
cd meat-suit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
cp .env.example .env
# fill in LLM_API_KEY, optionally edit TIMEZONE and the sleep cutoffs
python run.py
```

Open `http://127.0.0.1:5050/`. First boot drops you on a setup screen to pick a 4-12 digit passcode. After that it's a login screen, then the chat.

One secret to fill in:

**LLM_API_KEY.** Default config uses Gemini's free tier. Grab one at <https://aistudio.google.com/apikey> with a Google account, no card required. Plenty for one-user daily use (1500 req/day on Flash).

Want OpenAI, Groq, or OpenRouter instead? Same SDK, different base URL. Set `LLM_BASE_URL` and the model names in `.env`. Examples are commented in `.env.example`.

The cookie signing key (`MEAT_SUIT_COOKIE_SECRET`) auto-generates on first boot and writes itself back into `.env`. If you upgraded from a pre-passcode build, the legacy `MEAT_SUIT_WEB_KEY` is read as a fallback so your existing session stays valid.

### Forgot the passcode

```bash
python -m meat_suit.reset_passcode
```

That wipes the stored hash and clears the lockout. Next boot puts you back on the setup screen.

### Lockout policy

Five wrong passcodes locks the app for 15 minutes. The lockout is global (single-tenant). The reset command above clears it.

---

## Schedule (in your timezone)

- Every 30 min — Scribe pass on new chat exhaust
- 11:00 — anti-PDA nudge (skip with `NUDGES_ENABLED=0`)
- 17:30 — anti-PDA nudge
- 23:59 — Weaver writes and delivers the daily entry
- 22:00 Sunday — weekly Weaver delivers the pattern paragraph
- At each sleep cutoff — sleep nudge if you're still messaging past it

Nudges land in the chat as `system_nudge` rows. They show up the next time the chat tab refreshes (the chat polls every 30s while open). No push notification, no badge. If the tab is closed you'll see them next time you open it.

---

## Time awareness

The Persona gets a small time note on every reply: what day it is, what part of the day it is, whether it's a weekend evening, and whether you're past your sleep cutoff. It also gets told when you've been gone for several days, with explicit instructions not to greet you or ask where you went.

A nightly job watches the clock. If you're still messaging past your weeknight cutoff (default 23:00) or weekend cutoff (default 01:00), the active persona drops a single in-character sleep observation that you can ignore. It will not nag twice the same night. The "snooze tonight" button in Settings kills only that night's sleep nudge.

The Weaver also gets two small extras: a short rhythm summary of when the day's chat clustered, and the dates of any late-night sessions inside the week it's writing about. It uses these as texture, not as judgment.

---

## Privacy

Everything lives in `./data/meatsuit.db`. Local SQLite, single file, no cloud anything.

The passcode is stored as an argon2id hash. The session cookie is httpOnly, signed, and expires 30 days after your last visit.

The Persona only ever sees the rolling 20-message chat window. It never reads journals, signals, or polls. That part is on purpose.

The full JSON export under Settings dumps everything: messages, journal, vibe history, signals, sleep events, prefs.

---

## Exposing it (optional)

Default bind is `127.0.0.1:5050`, localhost only. If you want to reach the app from your phone or another machine, run it behind a tunnel:

- Cloudflare Tunnel
- Tailscale Funnel
- ngrok

Set `WEB_BIND_HOST=0.0.0.0` and `WEB_COOKIE_SECURE=1` only when the tunnel terminates HTTPS for you. Anything else and the cookie won't make the round trip.

---

## File map

```
meat-suit/
├── run.py                      # boots FastAPI on uvicorn
├── requirements.txt
├── .env.example
├── scripts/
│   └── check_llm.py            # one-shot probe: does the LLM endpoint reply?
├── meat_suit/                  # python backend
│   ├── api.py                  # FastAPI app, auth + all routes + scheduler lifespan
│   ├── config.py               # env loader
│   ├── db.py                   # sqlite schema + helpers
│   ├── scheduler.py            # APScheduler jobs (Scribe / Weaver / nudges / sleep)
│   ├── time_ctx.py             # time + message-context notes for persona/weaver
│   ├── nudges.py               # bait pools per persona
│   ├── reset_passcode.py       # CLI: wipe stored hash + lockout
│   ├── personas/
│   │   └── catalog.py          # 4 personas as system prompts
│   └── minds/
│       ├── persona.py          # mind 1, chat
│       ├── scribe.py           # mind 2, json extraction
│       └── weaver.py           # mind 3, midnight ghostwriter
├── web/                        # SPA, served by FastAPI on the same origin
│   └── client/                 # vite + react + tailwind + shadcn
└── legacy/                     # the old telegram bot, kept for reference
    ├── bot.py
    ├── body_check.py
    └── low_spoon.py
```

The Telegram bot was the original front end. It's parked under `legacy/` and no longer wired into anything. The web app is the only entry point now.

---

## License

MIT. Built slow. Built quiet.
