# Meat-Suit Web

A browser companion for the Meat-Suit telegram bot. Ports **Mind 1** (the persona) into a web app: landing page plus interactive demo with all four vibes.

The scribe (Mind 2), weaver (Mind 3), and eye (Mind 4) only live in the python version (`../`). They need a long-running cron, a personal Telegram chat, and a stable home folder. The web version is just the part you talk to.

## what's here

- **Landing page** explaining the quad-mind architecture, the four personas, and what the system does sideways.
- **Interactive demo** at `/#/demo` where you can switch between the four vibes. Each vibe keeps its own conversation alive on the server. Refresh-safe. Tap "fresh" to wipe a single vibe.

## stack

- Express + Vite + React + Tailwind + shadcn/ui primitives
- SQLite via better-sqlite3 + Drizzle ORM (per-persona session state, server-side)
- OpenAI Responses API for chat (model: `gpt_5_1`)

## run locally

```bash
npm install
npm run dev
```

Server starts on port 5000. Visit `/` for the landing, `/#/demo` for the chat.

The chat endpoint expects the Perplexity LLM API proxy. If you're running this outside that environment, swap `process.env.OPENAI_BASE_URL` and `OPENAI_API_KEY` in `server/routes.ts` for whatever endpoint you have.

## structure

```
client/          react frontend
  src/pages/     Landing.tsx, Demo.tsx, not-found.tsx
  src/components/  Layout, Logo
shared/          schema (chat_sessions, messages)
server/          express routes, sqlite storage, persona system prompts
```

The persona system prompts in `server/personas.ts` are ported verbatim from `../meat_suit/personas/catalog.py`, including the anti-PDA clause and the 2026 anti-AI-tell rules.

## what it deliberately doesn't do

- No accounts. No auth. No user identity.
- No journal. No nightly Weaver. No weekly look-back. Those need a long-running cron and a personal home folder.
- No streaks. No leaderboards. No analytics.

It's the front-end vibe, on the web. That's it.
