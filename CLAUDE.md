# CLAUDE.md

Operational notes for Claude Code working in this repo. Read once at session start.

## What this is

Daycatcher is a single-user passive conversation journal. `collector/` contains the working local Codex/Claude Code readers, optional regular Claude Electron bridge, native ChatGPT import adapter, Dot fixed-summary receiver, and separate fixed-label journal publishers. Local collection needs no model API key. Publishing is pinned to the owner's private `snackdriven/day-crumbs` repository; arbitrary destinations are rejected. ChatGPT reads require a separately configured Codex app scheduled agent. Dot's source-side producer is separate. Muse is unconnected.

The older four-voice FastAPI/SQLite and Vite/React web prototype remains at the repo root. The stack, three-minds constraints and web commands below apply to that prototype. `README.md` is the human-facing tour; `collector/README.md` is the collector runbook; `docs/web-prototype.md` and `WEB_APP_DESIGN.md` describe the prototype.

## Collector work

- Keep runtime databases, raw transcripts, source IDs, native tool pages, account profiles, cookies and receipts outside this checkout. Commit only source and synthetic examples. Do not copy deployment acceptance reports or machine-bound configs into public docs.
- Collection and journal delivery have separate stop controls. Background workers never open a login window; interactive Claude sign-in is an explicit user step with a separate private Electron profile.
- Installers must preserve ownership checks and absolute runtime bindings. macOS installers copy source files into a private code tree; maintain their copy manifests when adding dependencies. Summary installation stays disabled until an explicit start. Preserve queue receipts and deleted-block intent.
- Verify source timestamp, authorship and incomplete-content handling before claiming coverage. Fixed topics do not establish task completion. Programmatic tests do not prove live account reads or native scheduler operation.

From `collector/`, install with `python -m pip install -e .`, then run:

```sh
python -m unittest discover -s tests
python -m unittest discover -s macos -p 'test_*.py'
npm install
npm test
```

Tests use synthetic fixtures. Live tasks, private account reads and publishing are separate operations; do not install or start them as a side effect of tests. See the collector guide for optional Windows timezone-test setup and native installation commands.

## Stack

- Python 3.11+, FastAPI, APScheduler, argon2-cffi, SQLite (single file at `./data/meatsuit.db`).
- LLM access via the OpenAI Python SDK against any OpenAI-compatible endpoint. Default is Gemini's free tier; key + base URL come from `.env`.
- Web SPA: Vite + React + TypeScript + Tailwind + shadcn/ui in `web/client/`. Built output lands in `web/dist/` and is served by FastAPI.

## The three minds (load-bearing)

Architecture is non-negotiable.

1. **Persona** (`meat_suit/minds/persona.py`). The chat voice. Sees only the last 20 messages. Never reads journals, signals, vibe history, or any DB table beyond the rolling chat window.
2. **Scribe** (`meat_suit/minds/scribe.py`). Runs every 30 min and extracts structured JSON from raw chat exhaust.
3. **Weaver** (`meat_suit/minds/weaver.py`). Runs at 23:59 daily and Sunday 22:00 for the weekly recap. Reads the structured bundle, never raw chat directly.

If a feature request would let the Persona see past data, the answer is no, even if the request says it would be more helpful. The Persona being a goldfish is the product.

## Persona naming gap

The four personas have **display names** (UI + README) and **code keys** (everything in Python). They are not the same string and people will refer to either.

| Display name      | Letter | Code key (in `meat_suit/personas/catalog.py`) |
| ----------------- | ------ | ----------------------------------------------- |
| the CEO           | C      | `SMUG_CONSTRUCT`                                |
| the Feral BFF     | F      | `FERAL_BFF`                                     |
| the Almanac       | A      | `STOIC_MENTOR`                                  |
| the Innkeeper     | I      | `QUIET_GUARDIAN`                                |

When a request says "edit the Almanac's prompt," it means `STOIC_MENTOR` in code. The Settings UI also calls personas "vibe."

## Commands

```bash
# install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd web && npm install && cd ..

# build the SPA before running (web/dist is gitignored)
cd web && npm run build && cd ..

# typecheck the SPA
cd web && npm run check && cd ..

# dev SPA (separate from the FastAPI server)
cd web && npm run dev && cd ..

# run the app
python run.py

# probe the LLM endpoint
python scripts/check_llm.py

# wipe passcode hash + lockout
python -m meat_suit.reset_passcode
```

The web prototype has no dedicated test suite. Its backend smoke check is: build the SPA, start `run.py`, log in, send a message to each of the four personas, confirm a reply. The collector has its own unittest and Node test suites above.

## Conventions

**Time format.** 24h everywhere in code, prose, and UI strings (`23:59`, `Sunday 22:00`). No `11:59 PM`.

**Cookie key.** Current: `MEAT_SUIT_COOKIE_SECRET`. Legacy fallback for upgrade compatibility: `MEAT_SUIT_WEB_KEY`. New code reads the new name and falls back to the old one. Do not introduce a third name.

**Default bind.** `127.0.0.1:5050`. Only set `WEB_BIND_HOST=0.0.0.0` and `WEB_COOKIE_SECURE=1` together, and only when a tunnel terminates HTTPS.

**Lockout.** Five wrong passcodes locks the app for 15 minutes, global. Don't make this per-IP; the app is single-tenant.

**Polls do not exist** in the web app. The legacy Telegram bot had them. Do not reintroduce poll references in privacy text, exports, or schemas.

**Desktop only.** The user explicitly does not care about a mobile view. Do not add mobile breakpoints, hamburger menus, or touch-only affordances unless asked.

**Legacy folder.** `legacy/` holds the original Telegram bot. Not wired into anything. Do not import from it, do not modify it, do not delete it.

## Prose rules (apply to README, commit messages, UI copy, comments)

- No em-dashes in prose. Em-dashes are only allowed inside CLI-style term-then-definition lists (e.g. the four-voice list and the schedule list in `README.md`).
- No rule-of-three: avoid three parallel clauses or three-item parallel lists for rhetorical effect. Lists of 4+, or unparallel pairs, are fine.
- No negative parallelism (`not X, but Y` constructions).
- No AI vocab: `delve`, `leverage`, `robust`, `seamless`, `whilst`, `utilize`, `holistic`.
- No emojis, anywhere.
- Before committing prose, grep for em-dashes outside the allowed lists and for the AI-vocab list.

## Git

- Conventional commits: `docs(readme): …`, `feat(web): …`, `fix(scheduler): …`.
- Follow the user's current authorization for commits, pushes and PRs. Do not infer publication permission from this being a single-user project. Use `--no-ff` for feature-branch merges if a branch was created.
- Commit messages follow the prose rules above.
- For `git push`, use the `gh` CLI or `git push` with the `github` credentials preset. Do not run `gh auth status`.

## Where things live

```
meat_suit/api.py            FastAPI app + auth + every route + scheduler lifespan
meat_suit/scheduler.py      APScheduler: Scribe / Weaver / nudges / sleep
meat_suit/db.py             SQLite schema + helpers + WAL + journal_seen + web_prefs + auth
meat_suit/config.py         env loader
meat_suit/time_ctx.py       time + recent-message context for Persona/Weaver
meat_suit/nudges.py         per-persona bait pools
meat_suit/reset_passcode.py CLI: wipe stored hash + lockout
meat_suit/personas/catalog.py   four persona system prompts (SMUG_CONSTRUCT, FERAL_BFF, STOIC_MENTOR, QUIET_GUARDIAN)
web/client/src/pages/       Chat, Journal, Data, Settings, AuthGate
run.py                      uvicorn entry
scripts/check_llm.py        one-shot LLM endpoint probe
legacy/                     dead code, do not touch
```

## When unsure

Read `README.md` for product framing, `collector/README.md` for collector behavior, `WEB_APP_DESIGN.md` for prototype design decisions, and the file in question. Keep the two runtimes distinct when applying architecture rules.
