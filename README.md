# Project Meat-Suit

Every app that wants to know about your life makes you tell it. "How are you feeling, on a scale of 1 to 10?" The act of being asked is the act that breaks the data.

Meat-Suit takes the question out of the room.

You open a chat tab. There are four AI voices in it, and you pick whichever one you can stand that day. They riff with you. They don't survey you. They have no idea anything is being recorded. As far as the chatting voice knows, the conversation evaporates the second you close the tab.

Behind a wall that voice can't see through, a second program reads what you typed and pulls structure out of the exhaust. A third program waits until 23:59 and writes the day's journal entry in a voice trained on how you actually write. The same program runs again Sunday at 22:00 and writes a paragraph about the whole week. The chatting voice has no access to any of them and no idea they exist.

What you get back is a journal you didn't write. The Data tab fills itself in. The Sunday paragraph knows things about your week you'd already forgotten. None of which you had to earn.

---

## The three minds

|     | Mind        | Job                                       | Sees                            |
| --- | ----------- | ----------------------------------------- | ------------------------------- |
| 1   | **Persona** | Banters with you in the chat tab          | the last 20 messages, nothing else |
| 2   | **Scribe**  | Extracts JSON from chat exhaust every 30 min | raw transcripts only         |
| 3   | **Weaver**  | Writes the daily journal at 23:59, the weekly recap on Sunday at 22:00 | the day or week's structured bundle |

The Persona is a goldfish with opinions. Its memory is the last 20 messages. Scribe and Weaver are downstream of it and invisible to it.

---

## The four voices

- `the CEO` (C) — calm, clipped, faintly amused. Directives over hand-holding.
- `the Feral BFF` (F) — loud, lowercase, will roast you with love. Minimal punctuation.
- `the Almanac` (A) — spare, observational, steady cadence. Very few questions.
- `the Innkeeper` (I) — soft, attentive, low-volume kindness. Hospitality cues.

---

## The surfaces

**Chat.** Letter switcher in the top-right (`C / F / A / I`) swaps voices. The `?` opens a card explaining who's who. A check-in button drops a single in-character status line, no follow-up.

**Journal.** Daily entries newest-first, with an unread dot when the Weaver ships one. Sunday entries get a weekly look-back ribbon.

**Data.** Quiet dashboard: total messages, current streak, percent-after-23:00, vibe distribution, sleep adherence dots, hourly density, this-week-vs-last-week.

**Settings.** Default vibe (the UI's word for persona), sleep windows, nudge toggles, "snooze tonight," full JSON export, and a lock button that clears your session cookie.

---

## Setup

```bash
git clone https://github.com/snackdriven/meat-suit.git
cd meat-suit
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
cp .env.example .env
python run.py
```

Open `http://127.0.0.1:5050/`. First boot is a setup screen for a 4-12 digit passcode. After that it's a login screen, then the chat.

**LLM_API_KEY.** Default is Gemini's free tier. Grab one at <https://aistudio.google.com/apikey>, no card. Want OpenAI, Groq, or OpenRouter? Set `LLM_BASE_URL` and the model names; examples are commented in `.env.example`.

The cookie key (`MEAT_SUIT_COOKIE_SECRET`) auto-generates on first boot and writes itself to `.env`.

Forgot the passcode? Run `python -m meat_suit.reset_passcode` to wipe the hash and clear the lockout.

**Lockout.** Five wrong passcodes locks the app for 15 minutes, global.

---

## Schedule (in your timezone)

- Every 30 min — Scribe pass on new chat exhaust
- 11:00 — anti-PDA nudge (skip with `NUDGES_ENABLED=0`)
- 17:30 — anti-PDA nudge
- 23:59 — Weaver writes and delivers the daily entry
- Sunday 22:00 — Weaver delivers the weekly pattern paragraph
- At each sleep cutoff — sleep nudge if you're still messaging past it

Nudges land in the chat as `system_nudge` rows on the next refresh. No push, no badge.

---

## Time awareness

The Persona gets a small time note on every reply: day, part of day, whether it's a weekend evening, whether you're past your sleep cutoff. It's also told when you've been gone for several days, with instructions not to greet you or ask where you went.

A nightly job watches the clock. If you're still messaging past your weeknight cutoff (default 23:00) or weekend cutoff (default 01:00), the active persona drops a single in-character sleep observation. It won't nag twice the same night. "Snooze tonight" in Settings kills only that night's nudge.

The Weaver also gets a rhythm summary of when the day's chat clustered and the dates of any late-night sessions in the week it's writing about. Texture, not judgment.

---

## Privacy

Everything lives in `./data/meatsuit.db`. Local SQLite, single file, no cloud. The passcode is an argon2id hash; the session cookie is httpOnly, signed, and expires 30 days after your last visit. The full JSON export under Settings dumps everything: messages, journal, vibe history, signals, sleep events, prefs.

---

## Exposing it (optional)

Default bind is `127.0.0.1:5050`, localhost only. To reach it from your phone, run it behind a tunnel: Cloudflare Tunnel, Tailscale Funnel, or ngrok. Set `WEB_BIND_HOST=0.0.0.0` and `WEB_COOKIE_SECURE=1` only when the tunnel terminates HTTPS.

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
└── legacy/                     # original telegram bot, not wired into anything
```

---

## License

MIT.
