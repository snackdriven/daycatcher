# Meat-Suit Web

a small web companion that ports mind 1 (the persona) of the Meat-Suit telegram bot
into the browser. landing page + interactive demo with all four vibes.

the scribe and the weaver still live in the python version (../).

## stack

- express + vite + react + tailwind
- shadcn/ui primitives
- sqlite via better-sqlite3 + drizzle (per-persona session state, server-side)
- openai responses api for chat (model: gpt_5_1)

## run locally

```bash
npm install
npm run dev
```

server starts on port 5000. visit `/` for the landing, `/#/demo` for the chat.

note: the chat endpoint expects the perplexity LLM API proxy. if you're running this
outside that environment, swap `process.env.OPENAI_BASE_URL` and `OPENAI_API_KEY` in
`server/routes.ts` for whatever endpoint you have.

## structure

```
client/        react frontend
  src/pages/   Landing.tsx, Demo.tsx, not-found.tsx
  src/components/  Layout, Logo
shared/        schema (chat_sessions, messages)
server/        express routes + sqlite storage + persona system prompts
```

personas in `server/personas.ts` are ported verbatim from `../meat_suit/personas/catalog.py`.
