# Daycatcher Web App: Design Spec

Status: **LOCKED. Building from this.**
Branch: `gemini-llm` (web app changes ride alongside Gemini swap).

## Decisions made out loud (after challenging the first draft)

| Question | Decision |
|---|---|
| Express vs FastAPI | **Drop Express.** FastAPI serves both `/api` and the built SPA. One process. |
| Cron / scheduler home | **Moves into the FastAPI process** via APScheduler, started/stopped in the FastAPI lifespan. Lives in `meat_suit/scheduler.py`. |
| Bind | `127.0.0.1` for local dev. Configurable via `WEB_BIND_HOST`. |
| Public access | **Cloudflare Tunnel** when the user is ready (`cloudflared` on the laptop). Eventual hostname like `meat.snackdriven.com`. The app doesn't need to know it's tunneled. |
| Auth | **Required from day one.** Single shared secret `MEAT_SUIT_WEB_KEY` in `.env` (auto-generated on first boot if missing). One-time `?key=...` URL sets a signed httpOnly cookie (1y expiry, `Secure`, `SameSite=Lax`). Recommended addition later: Cloudflare Access on the tunnel for an extra factor. |
| PWA badge / SW | **Dropped.** Manifest only. Unread state shown as a dot in the nav. |
| WebSocket | **None in v1.** Replies returned synchronously from `POST /api/messages`. Journal/data refresh on tab focus. |
| Mood | **Private terminal meets reading room.** Newsreader for the journal only. Inter Tight for chat + shell. JetBrains Mono for /data numbers. Persona colors restrained — 1px accent strip on assistant messages, switcher fill, persona sheet dot. Nowhere else. |
| /personas page | **Killed as a route.** Becomes a sheet that opens from the switcher's `?` button. Auto-shown once on first visit. |
| Persona switcher | Letter-marks (C, F, A, I) in a segmented control. Active fill = persona color. Tooltip + byline above the chat stream. |
| Mid-thread switch | Allowed. Inject one-line system note on next reply: “you are now X, do not reference earlier voice or apologize.” |
| DB | Reuse `data/meatsuit.db`. **WAL mode + 5s busy_timeout** in `db.py` so the request handlers and the in-process scheduler don't fight each other. |
| Pages | **Four:** `/` chat, `/journal`, `/data`, `/settings`. |
| Build order | UI shell first against current persona keys (`smug_construct`/`feral_bff`/`stoic_mentor`/`quiet_guardian`). The CEO/Feral/Almanac/Innkeeper rewrite is its own commit and does not block UI work. |
| Weaver delivery | **Pull-only.** Weaver still writes at 23:59, but no TG push when web is the only surface. The unread nav dot is the entire “notification.” |

---

## 1. What this is

A private web app. The only surface for the Daycatcher brain. Telegram is gone (the old handlers are parked under `legacy/` for one commit so the diff is legible, then deleted). Everything new lives here.

**What it is not:**
- Not multi-user. One person, one brain. No accounts, no signup, no avatars.
- Not a chatbot UI. Chat is one room. The journal, the data, the persona reference — those are the building.
- Not Character.AI. The personas are stylized voices, not partners. The stylization is the safety feature.

---

## 2. Architecture

```
┌────────────────────────────┐         ┌────────────────────────────┐
│  Browser (React/Vite SPA)  │  HTTPS  │  Python brain (FastAPI)    │
│  client/src/...            │ ──────▶ │  meat_suit/api.py          │
│  - chat, journal, /data,   │         │  wraps Persona/Scribe/     │
│    settings                │         │  Weaver + db.py +          │
│  - persistent persona row  │ ◀────── │  scheduler.py (in-process) │
│  - PWA manifest only       │ reply  │                            │
└────────────────────────────┘         └────────────────────────────┘
                                                  │
                                                  ▼
                                       ┌────────────────────────────┐
                                       │  data/meatsuit.db (WAL)   │
                                       │  messages, extractions,   │
                                       │  journal_entries, vibe,   │
                                       │  + journal_seen, web_prefs│
                                       └────────────────────────────┘

         (later) browser ◀─ Cloudflare Tunnel ─▶ 127.0.0.1:5050
         meat.snackdriven.com (or similar) terminates at the laptop
```

**Single process.** uvicorn runs FastAPI. FastAPI mounts the built React SPA as static files at `/`. APScheduler runs in the same event loop, started in the lifespan and stopped on shutdown. No Express, no Node server, no separate worker, no Telegram. One reply path: `POST /api/messages` blocks until the persona returns and writes the assistant row, then returns it.

**What the existing `web/` directory becomes:** rebuilt from scratch using the standard webapp template. Its current contents (its own SQLite, its own personas, its own LLM call, the Express routes) don't survive the migration and get deleted. The new `web/` is a Vite/React/Tailwind SPA only — no backend code in there. The backend lives in `meat_suit/api.py`.

---

## 3. Pages & navigation

**Four routes:**

| Route | Purpose | Persona-styled? |
|---|---|---|
| `/` | Chat. The default room. Persistent persona switcher in top-right. | Yes — current vibe accents assistant messages |
| `/journal` | Weaver's nightly entries + weekly reflections. Reverse chron. | No (voice-neutral, Newsreader serif) |
| `/data` | Full dashboard. KPIs, vibe distribution, sleep adherence, hourly density, weekly compare. | No (voice-neutral, mono numbers) |
| `/settings` | Vibe selector, sleep windows, anti-PDA times, export, danger zone. | No |

The old fifth page (`/personas`) is now a sheet that opens from the persona switcher's `?` button. Auto-shown once on first run.

**Nav model:** left sidebar on desktop (icon + label), bottom tab bar on mobile (icon only). Active route gets a 2px accent strip. The persona switcher is in the chat header only — doesn't make sense on /journal or /data.

**Unread:** when a Weaver entry exists that hasn't been viewed, the journal nav item shows a small dot. Cleared on /journal visit. No browser notification, no service worker badge.

---

## 4. Art direction — Studio / Atelier

The wrong move here is "soft therapy app." Pastel gradients, Inter, lots of "you've got this." That's the AI aesthetic and it betrays what the product actually is.

The right move is **editorial**. Think a literary quarterly's website crossed with a private terminal. Generous margins. Real typographic contrast. Each persona has a color signature so the corner switcher means something.

### Palette

**Base (shared across pages):**
- `--bg`: warm near-black, `28 8% 8%` (a touch of warmth, not pure neutral)
- `--surface`: `28 6% 12%`
- `--surface-2`: `28 6% 16%`
- `--ink`: `40 15% 92%` (paper-white, slightly creamy)
- `--ink-muted`: `40 8% 65%`
- `--ink-quiet`: `40 6% 45%`
- `--rule`: `28 6% 22%` (hairlines)
- `--accent`: `40 60% 70%` (warm gold — used very sparingly, mainly focus rings + active states)

**Light mode:** mirror — `40 25% 96%` paper, `28 10% 12%` ink, same accent. Both modes shipped.

**Persona signatures (used only in chat, persona switcher, /personas):**
- **CEO** — `15 75% 58%` (burnt orange, performative warmth)
- **Feral BFF** — `280 35% 65%` (dusty mauve, late-night mood)
- **The Almanac** — `35 30% 55%` (faded khaki, archival)
- **The Innkeeper** — `145 18% 55%` (slow green, hearth)

These are voice colors, not UI colors. They show up:
- As a 1px left border on assistant messages in the active persona
- As the active state of the corner persona switcher
- As the avatar dot on /personas
- Nowhere else. The shell stays warm-neutral.

### Typography

- **Display / headings:** [Newsreader](https://fonts.google.com/specimen/Newsreader) (variable serif, optical sizing). Used for page titles, persona names, journal headlines. Italic at display sizes.
- **Body:** [Inter Tight](https://fonts.google.com/specimen/Inter+Tight). Tight tracking, low-key.
- **Mono (data + system):** [JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono). For /data numbers and timestamps.

Type scale (web app constraints — no `text-2xl`+ inside the app):
- `--text-xs` 12px / `--text-sm` 13px / `--text-base` 15px / `--text-lg` 17px / `--text-xl` 22px (max in-app)
- Hero on /personas page only: `--text-2xl` 28px

### Motion

Minimal and functional. Page transitions are 150ms cross-fade. New message slides up 6px and fades in over 220ms with `cubic-bezier(0.2, 0, 0, 1)`. The persona switcher's color change is 200ms. No bounces, no springs, no parallax.

### Chrome

Hairlines, not borders. `1px solid var(--rule)` everywhere. Round corners exist but are tight (4–6px). No drop shadows except a single `0 1px 0 var(--rule)` separator on the top bar. No glassmorphism, no gradients beyond the persona signature accent strip on chat.

---

## 5. Data model (additions to the brain DB)

The Python brain owns all storage. The web client never touches SQLite directly. Existing tables stay (`messages`, `extractions`, `journal_entries`, `weekly_reflections`, `vibe_state`, `sleep_state`, `status_events`, `body_check_polls`, `rabbit_holes`, `photo_observations`). The poll and photo tables go inert in v1 (no writers) but the schema sticks around; if a web equivalent lands later it slots into the same shape. Drop them in a separate cleanup commit if they're still empty in 90 days.

New additions (already added in `db.py`):

```sql
-- Track which journal entries the user has seen, for the unread nav dot.
CREATE TABLE IF NOT EXISTS journal_seen (
    local_date TEXT PRIMARY KEY,        -- references journal_entries.local_date
    seen_at    TEXT NOT NULL
);

-- Web-app-specific key/value prefs (theme override, first-run-done, etc).
CREATE TABLE IF NOT EXISTS web_prefs (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
```

**Concurrency:** WAL mode + 5s busy_timeout enabled in `db.py`. The web request thread, the scheduler thread, and any debug shell can all hit the DB without `SQLITE_BUSY` storms.

---

## 6. Backend API surface

New module: `meat_suit/api.py`. uvicorn binds to `WEB_BIND_HOST:WEB_PORT` (default `127.0.0.1:5050`). FastAPI mounts the built SPA at `/`, all routes below at `/api/*`.

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/state` | Bootstrap: current vibe, sleep window, unread journal count, last message id |
| `GET`  | `/api/personas` | Persona catalog (display only — never system prompts) |
| `POST` | `/api/vibe` | Set active vibe |
| `GET`  | `/api/messages?since_id=...&limit=200` | Messages newer than `since_id`, or last `limit` if omitted |
| `POST` | `/api/messages` | Send a user message; returns the assistant reply synchronously |
| `DELETE` | `/api/messages` | Reset chat history |
| `GET`  | `/api/journal?limit=30&before=YYYY-MM-DD` | Journal entries (daily + weekly), newest-first |
| `POST` | `/api/journal/{local_date}/seen` | Mark entry as seen (clears the unread dot) |
| `GET`  | `/api/data/summary?range=7d\|30d\|90d\|all` | Dashboard payload (see §8) |
| `GET`  | `/api/settings` | Settings blob |
| `PATCH` | `/api/settings` | Update sleep windows, anti-PDA toggle, etc. |
| `POST` | `/api/sleep/snooze` | Skip tonight's sleep nudge |
| `POST` | `/api/sleep/off-forever` | Disable sleep nudges entirely |
| `POST` | `/api/check` | On-demand status check (composes a nudge in the active voice) |
| `GET`  | `/api/export` | Full data export as JSON download |
| `GET`  | `/api/auth/login?key=...` | Sets the signed cookie. Redirects to `/`. |
| `POST` | `/api/auth/logout` | Clears the cookie. |

**Auth middleware:** every `/api/*` route except `/api/auth/login` requires a valid `meat_suit_session` cookie signed with `MEAT_SUIT_WEB_KEY` via `itsdangerous.URLSafeTimedSerializer`. Missing/invalid cookie → 401 with body `{"error": "unauthorized"}`. Frontend catches 401 and shows a paste-your-key screen.

**No WebSocket in v1.** Replies return synchronously. Journal/data refresh on tab focus + manual refresh button.

**Same-origin only.** No CORS configuration needed: Vite dev server proxies `/api` to FastAPI; in prod FastAPI serves both the SPA and the API.

---

## 7. Page designs

### 7.1 `/` — Chat

```
┌──────────────────────────────────────────────────────────────┐
│ [logo]  Daycatcher             [CEO][Feral][Alm][Inn]   ☾   │  top bar
├──────┬───────────────────────────────────────────────────────┤
│      │                                                       │
│  /   │     Today, 3:41 PM                                    │
│  📖  │   ▌  you said something here                          │  user msg (right)
│  📊  │                                                       │
│  🎭  │   ▌  CEO replies. Vertical accent strip on left =     │  assistant msg (left)
│  ⚙   │      the active persona's color.                      │
│      │                                                       │
│      │   [composer ──────────────────────────────────] [↵]  │
└──────┴───────────────────────────────────────────────────────┘
```

- Top bar: logo (custom SVG), product name in Newsreader italic, **persistent persona switcher** (4 small icons + 1px underline in the active persona's color), theme toggle.
- Left rail: 4 nav icons (chat / journal / data / settings). Active route gets a 2px-wide accent strip on its left edge.
- Message stream: reverse chron at bottom (chat-style). Date dividers between days. User messages right-aligned, no avatar, no bubble — just text indented with a thin right rule. Assistant messages left-aligned with a 2px left rule in the persona's color and the persona name in Newsreader italic + size-xs above the first message of a turn.
- Composer: single line that grows. Cmd/Ctrl-Enter sends. No emoji picker, no formatting toolbar, no attachments in v1. Photo intake doesn't exist in either surface anymore — if it comes back it comes back as a web upload, separate commit.
- **Persona switch mid-thread:** clicking a different persona does NOT start a new thread. Same chat continues; next assistant reply uses the new voice. A subtle inline marker (`— switched to Feral BFF —` in `--ink-quiet`) is rendered between messages.

### 7.2 `/journal` — Weaver's room

```
┌──────────────────────────────────────────────────────────────┐
│ Journal                                  [Daily][Weekly]    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   Tuesday, April 28                                          │
│   ───────────────────                                        │
│   The day started slow. By noon I'd remembered I haven't     │
│   eaten since yesterday morning, and the back of my neck     │
│   has been doing that thing. ...                             │
│                                                              │
│   ★ NEW                                                       │
│                                                              │
│   Monday, April 27                                           │
│   ───────────────────                                        │
│   I forgot the dentist again. ...                            │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

- Reverse-chron list. No infinite scroll — paginate by week.
- Two filter chips top-right: Daily / Weekly. Default = both interleaved.
- Each entry renders in Newsreader (the journal is the most "literary" surface). Date in italic small caps.
- A horizontal rule under the date, then the body text in ~17px line-height 1.6.
- New-since-last-visit: small `★ NEW` marker in `--accent`. First viewport visit on this page calls `POST /api/journal/:id/seen` for everything visible.
- Empty state: "Weaver writes after midnight. Come back tomorrow." in italic, centered, in `--ink-quiet`.

### 7.3 `/data` — Dashboard (full)

Voice-neutral. Mono numbers. No persona color anywhere.

```
┌──────────────────────────────────────────────────────────────┐
│ Data                              [7d][30d][90d][All]        │
├──────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │
│  │  MESSAGES   │  │   STREAK    │  │  AFTER 11PM │           │
│  │    247      │  │    14d      │  │     38%     │           │
│  │   ▁▂▅▇▃▂▁    │  │             │  │             │           │
│  └─────────────┘  └─────────────┘  └─────────────┘           │
│                                                              │
│  Vibe distribution                                           │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ CEO     ▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░ 41%         │   │
│  │ Feral   ▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░ 24%         │   │
│  │ Almanac ▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░ 19%         │   │
│  │ Innkpr  ▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░ 16%         │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  Sleep window adherence                                      │
│  [horizontal timeline showing each night, green = within,    │
│   amber = late, red = past 2am]                              │
│                                                              │
│  Message density by hour          Weekly comparison          │
│  [24-hour ridgeline]              [this week vs last]        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

KPIs as big mono numbers + small sparkline. Then four chart blocks:
1. **Vibe distribution** — horizontal bars, mono percentages.
2. **Sleep adherence** — calendar heatmap (rows = weeks, cols = days), color = how late.
3. **Hourly density** — area chart, 0–23 on x.
4. **Weekly comparison** — two columns (this vs last) for messages, late nights, vibes.

Charts via Recharts. Numbers in JetBrains Mono. No persona names anywhere — `vibe_distribution` shows display names but renders neutrally.

### 7.4 Persona reference sheet (not a route)

Opens from the `?` button next to the persona switcher in the chat header. Auto-shown once on first run; the `web_prefs` row `personas_seen=1` suppresses it after that. A shadcn `Sheet` component sliding in from the right on desktop, full-screen `Drawer` on mobile.

Contents: a 4-card stack. Each card:

```
┌───────────────────────────────────────────────┐
│ ●  THE CEO                                    │  (● = persona color)
│                                               │
│ Externalize the body as the asset. Perform    │
│ until you accidentally start caring.          │
│                                               │
│ ─────────────────────────────────────         │
│                                               │
│ Sample:                                       │
│   you: didn't sleep again                     │
│   CEO: OK so the asset is running on fumes.   │
│        That's a Q3 problem becoming a Q2      │
│        problem. Look, go drink water. I'm     │
│        not doing this with you.               │
│                                               │
│ Voice tells: "OK so" / "Look" / drops mask    │
│ once per ~4 replies                           │
└───────────────────────────────────────────────┘
```

Each card uses the persona's signature color for the dot and the heading underline. Body stays in --ink. This sheet is the only place all four colors are visible at once. No deep link, no URL — closing the sheet returns to wherever the user was.

### 7.5 `/settings`

Three sections:

1. **Voice** — vibe selector (radio, 4 options with same card style as /personas).
2. **Rhythm** — sleep window (weeknight/weekend cutoffs, two time pickers each), anti-PDA times (11:00 + 17:30, toggle on/off), nudge frequency.
3. **Data**: export (downloads `daycatcher_export_YYYY-MM-DD.json`), reset chat history (confirm), reset everything (double-confirm).

All forms use shadcn `Form` + Zod, mutations invalidate `/api/settings` and `/api/state`.

---

## 8. Dashboard payload (`GET /api/data/summary`)

```json
{
  "range": "30d",
  "kpis": {
    "messages_total": 247,
    "messages_sparkline": [3, 5, 12, 18, 9, 7, 4, ...],
    "streak_days": 14,
    "after_11pm_pct": 0.38
  },
  "vibe_distribution": [
    {"key": "ceo", "display": "The CEO", "pct": 0.41},
    {"key": "feral_bff", "display": "Feral BFF", "pct": 0.24},
    {"key": "almanac", "display": "The Almanac", "pct": 0.19},
    {"key": "innkeeper", "display": "The Innkeeper", "pct": 0.16}
  ],
  "sleep_adherence": [
    {"date": "2026-04-27", "last_msg_local": "23:47", "status": "within"},
    {"date": "2026-04-26", "last_msg_local": "01:14", "status": "late"}
  ],
  "hourly_density": [
    {"hour": 0, "count": 8}, {"hour": 1, "count": 3}, ...
  ],
  "weekly_compare": {
    "this_week": {"messages": 47, "late_nights": 3, "top_vibe": "ceo"},
    "last_week": {"messages": 51, "late_nights": 5, "top_vibe": "feral_bff"}
  }
}
```

All computation is server-side (Python, on top of `messages` table). The frontend just renders.

---

## 9. PWA shell

- `manifest.json` with name, short_name, theme_color = `--bg`, icons (256, 512, maskable).
- Installable on iOS/Android/desktop.
- **No service worker.** Vite builds a single SPA bundle, FastAPI serves it. Offline isn't a goal for v1 — the brain isn't reachable offline anyway.
- **No `setAppBadge`.** Unread state is the dot on the journal nav item. That's it. If badging matters later it goes in a separate commit, gated behind a flag, after the rest of the app is real.

---

## 10. Build order (what's left)

Everything in the decisions table is locked. Order of work from here:

1. ✅ `db.py` — WAL + busy_timeout + `journal_seen` + `web_prefs` + web/dashboard helpers
2. ✅ `meat_suit/scheduler.py` — APScheduler module (Scribe / Weaver / weekly Weaver / anti-PDA / sleep)
3. ✅ `config.py` — Telegram fields gone, web fields in, `MEAT_SUIT_WEB_KEY` auto-generates to `.env` on first boot
4. ✅ `legacy/` — `bot.py`, `body_check.py`, `low_spoon.py` moved out of the import path
5. **`meat_suit/api.py`** — FastAPI app, signed-cookie auth middleware, every `/api/*` route, scheduler start/stop in lifespan
6. **`run.py`** — boots uvicorn on `WEB_BIND_HOST:WEB_PORT`, no Telegram path
7. **`web/`** — wipe, scaffold from the standard webapp template, repoint at FastAPI
8. Pages, in order: chat → journal → data → settings
9. Wire FastAPI to serve the built SPA from the same origin
10. Local QA pass with Playwright
11. Commit, push to `gemini-llm`, deploy preview
12. Persona rewrite (CEO / Feral / Almanac / Innkeeper system prompts) — separate commit, separate session

**One archival note:** the old `web/` directory had its own SQLite, its own persona keys, and its own LLM call. None of that survives. It gets deleted in step 7. The reference copy is in git history on `gemini-llm` if anything's needed back.

---

## 11. What I'm NOT changing

- The brain prompts (Persona/Scribe/Weaver) — same code, same prompts, until the persona rewrite commit (step 12).
- SQLite as the single store. No Postgres, no Redis, no queue. One file at `data/meatsuit.db`.
- Single-user. No accounts. Don't add them later.
