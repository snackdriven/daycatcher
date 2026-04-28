# Meat-Suit Web

Browser companion for the [Meat-Suit](../) telegram bot. Ports **Mind 1** (the persona) into a web app so you can try the chat side without setting up a bot or a cron job. Default config uses Gemini's free tier so you don't need a paid LLM key either.

The scribe (Mind 2) and weaver (Mind 3) only live in the python version. They need a long-running cron, a personal Telegram chat, and a stable home folder. The web version is just the part you talk to.

## what's here

- **Landing page** explaining the architecture, the four personas, and what the system actually does.
- **Interactive demo** at `/#/demo`. Switch between the four vibes. Each vibe keeps its own conversation alive on the server. Refresh-safe. Tap "fresh" to wipe a single vibe.

## stack

- Express + Vite + React + Tailwind + shadcn/ui
- SQLite via better-sqlite3 + Drizzle ORM, server-side per persona
- OpenAI-compatible Chat Completions API. Defaults to Gemini, swap with two env vars.

## run locally

```bash
npm install
cp .env.example .env
# fill in LLM_API_KEY (free Gemini key from https://aistudio.google.com/apikey)
npm run dev
```

Server starts on port 5000. Visit `/` for the landing, `/#/demo` for the chat.

Want a different provider? `LLM_BASE_URL` + `LLM_MODEL` in `.env` point the same OpenAI SDK at OpenAI proper, Groq, OpenRouter, or anything OpenAI-compatible. See `.env.example` for examples.

## structure

```
client/          react frontend
  src/pages/     Landing.tsx, Demo.tsx, not-found.tsx
  src/components/  Layout, Logo
shared/          schema (chat_sessions, messages)
server/          express routes, sqlite storage, persona system prompts
```

The persona system prompts in `server/personas.ts` are ported verbatim from `../meat_suit/personas/catalog.py`.

## what it deliberately doesn't do

- No accounts. No auth. No user identity.
- No journal. No nightly Weaver. No weekly look-back. Those need a long-running cron and a personal home folder.
- No streaks. No leaderboards. No analytics.

It's the front-end vibe, on the web. That's it.
