"""SQLite layer. Stores raw chat exhaust, Scribe extractions, and Weaver journals.

Schema notes:
- `messages` is the source of truth: every inbound user message + every outbound persona reply.
- `extractions` holds Mind 2's JSON output, one row per scribe-pass.
- `journal_entries` holds Mind 3's nightly first-person narrative, one row per local day.
- `vibe_state` is a tiny key/value table for the active persona.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT    NOT NULL,
    local_date   TEXT    NOT NULL,         -- YYYY-MM-DD in user TZ; used by Scribe + Weaver
    role         TEXT    NOT NULL,         -- 'user' | 'assistant' | 'system_nudge'
    vibe         TEXT,                     -- active persona at the time
    content      TEXT    NOT NULL,
    scribed      INTEGER NOT NULL DEFAULT 0  -- 1 once Mind 2 has consumed this row
);

CREATE INDEX IF NOT EXISTS idx_messages_local_date ON messages(local_date);
CREATE INDEX IF NOT EXISTS idx_messages_scribed    ON messages(scribed);

CREATE TABLE IF NOT EXISTS extractions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT    NOT NULL,
    local_date   TEXT    NOT NULL,
    payload_json TEXT    NOT NULL          -- the full Mind 2 JSON blob
);

CREATE INDEX IF NOT EXISTS idx_extractions_local_date ON extractions(local_date);

CREATE TABLE IF NOT EXISTS journal_entries (
    local_date   TEXT PRIMARY KEY,         -- one entry per day
    ts_utc       TEXT NOT NULL,
    narrative    TEXT NOT NULL,
    source_json  TEXT NOT NULL             -- the merged JSON the Weaver consumed
);

CREATE TABLE IF NOT EXISTS vibe_state (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

-- v2: low-spoon button taps, body-check polls, rabbit holes, photo observations
CREATE TABLE IF NOT EXISTS status_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc     TEXT NOT NULL,
    local_date TEXT NOT NULL,
    state      TEXT NOT NULL,         -- 'overwhelmed' | 'non_verbal' | 'safe_food_only' | 'brain_fog'
    note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_status_events_local_date ON status_events(local_date);

CREATE TABLE IF NOT EXISTS body_check_polls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    local_date   TEXT NOT NULL,
    poll_id      TEXT UNIQUE NOT NULL,    -- Telegram poll id
    options_json TEXT NOT NULL,           -- list of option labels
    answers_json TEXT                     -- list of selected option indices, set on response
);
CREATE INDEX IF NOT EXISTS idx_polls_local_date ON body_check_polls(local_date);

CREATE TABLE IF NOT EXISTS rabbit_holes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc     TEXT NOT NULL,
    local_date TEXT NOT NULL,
    url        TEXT,
    raw_text   TEXT NOT NULL,
    topic      TEXT                       -- short topic phrase, set by Scribe / inline classifier
);
CREATE INDEX IF NOT EXISTS idx_rabbit_holes_local_date ON rabbit_holes(local_date);

CREATE TABLE IF NOT EXISTS photo_observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc       TEXT NOT NULL,
    local_date   TEXT NOT NULL,
    file_id      TEXT NOT NULL,           -- Telegram file_id (so we can re-fetch if needed)
    caption      TEXT,
    observation  TEXT NOT NULL,           -- Mind 4's neutral description
    signals_json TEXT NOT NULL            -- structured signals extracted from the image
);
CREATE INDEX IF NOT EXISTS idx_photo_obs_local_date ON photo_observations(local_date);

-- v3: sleep nudge state (snooze date, off-forever flag, last-nudged date)
CREATE TABLE IF NOT EXISTS sleep_state (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

-- v4 (web): track which journal entries the user has seen, for the nav unread dot.
CREATE TABLE IF NOT EXISTS journal_seen (
    local_date TEXT PRIMARY KEY,        -- references journal_entries.local_date
    seen_at    TEXT NOT NULL
);

-- v4 (web): web-app-only key/value prefs (theme, sidebar collapsed, first-run-done, etc).
CREATE TABLE IF NOT EXISTS web_prefs (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

-- v5 (web): single-row passcode auth. Replaces the env-var URL key.
CREATE TABLE IF NOT EXISTS auth (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    passcode_hash   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TEXT
);
"""


def _apply_pragmas(cx: sqlite3.Connection) -> None:
    """Pragmas that make concurrent access from web + optional TG bot safe.

    WAL lets multiple readers run while one writer is active; without it the
    second process gets SQLITE_BUSY on any write contention. busy_timeout makes
    contended writes wait briefly instead of erroring out.
    """
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=NORMAL")
    cx.execute("PRAGMA busy_timeout=5000")
    cx.execute("PRAGMA foreign_keys=ON")


def init_db(path: Path) -> None:
    with sqlite3.connect(path) as cx:
        _apply_pragmas(cx)
        cx.executescript(SCHEMA)
        cx.commit()


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    _apply_pragmas(cx)
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


# ---------- helpers ----------

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_message(
    db_path: Path,
    *,
    local_date: str,
    role: str,
    vibe: str | None,
    content: str,
) -> int:
    with connect(db_path) as cx:
        cur = cx.execute(
            "INSERT INTO messages (ts_utc, local_date, role, vibe, content) VALUES (?, ?, ?, ?, ?)",
            (_now_utc_iso(), local_date, role, vibe, content),
        )
        return int(cur.lastrowid)


def get_recent_messages(db_path: Path, limit: int = 20) -> list[sqlite3.Row]:
    """Recent dialog used as Persona context window (rolling)."""
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT role, content FROM messages "
            "WHERE role IN ('user','assistant') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return list(reversed(rows))


def get_message_timing_window(
    db_path: Path, *, limit: int = 50
) -> list[dict[str, str]]:
    """Recent (ts_utc, role) tuples for between-message awareness.

    Includes 'system_nudge' rows on purpose so the persona can detect when its
    own last outgoing message was an unprompted nudge (different from a reply).
    Newest-first. The caller passes this to time_ctx.build_message_context.
    """
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT ts_utc, role FROM messages "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"ts_utc": r["ts_utc"], "role": r["role"]} for r in rows]


def get_user_voice_samples(
    db_path: Path, *, limit: int = 12, min_chars: int = 12, max_chars: int = 280
) -> list[str]:
    """Pull recent user messages that read like the user's natural voice.

    Used to teach the Weaver (and optionally the Persona) the user's cadence,
    word choice, lowercase tendencies, run-ons, fragments. We skip very short
    button-tap-style lines and absurdly long pasted blocks because they don't
    represent how the user TALKS.

    Returns most-recent-first.
    """
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT content FROM messages "
            "WHERE role = 'user' "
            "AND length(content) BETWEEN ? AND ? "
            "ORDER BY id DESC LIMIT ?",
            (min_chars, max_chars, limit * 4),
        ).fetchall()
    samples: list[str] = []
    seen: set[str] = set()
    for r in rows:
        c = (r["content"] or "").strip()
        # Skip pure URLs / link-only lines — those are rabbit holes, not voice.
        if not c or c.lower().startswith(("http://", "https://")):
            continue
        # Skip dupes (button taps, repeated short replies).
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        samples.append(c)
        if len(samples) >= limit:
            break
    return samples


def get_unscribed_for_date(db_path: Path, local_date: str) -> list[sqlite3.Row]:
    with connect(db_path) as cx:
        return cx.execute(
            "SELECT id, ts_utc, role, content FROM messages "
            "WHERE local_date = ? AND scribed = 0 ORDER BY id ASC",
            (local_date,),
        ).fetchall()


def mark_scribed(db_path: Path, ids: list[int]) -> None:
    if not ids:
        return
    with connect(db_path) as cx:
        cx.executemany("UPDATE messages SET scribed = 1 WHERE id = ?", [(i,) for i in ids])


def insert_extraction(db_path: Path, local_date: str, payload: dict[str, Any]) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "INSERT INTO extractions (ts_utc, local_date, payload_json) VALUES (?, ?, ?)",
            (_now_utc_iso(), local_date, json.dumps(payload, ensure_ascii=False)),
        )


def get_extractions_for_date(db_path: Path, local_date: str) -> list[dict[str, Any]]:
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT payload_json FROM extractions WHERE local_date = ? ORDER BY id ASC",
            (local_date,),
        ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def upsert_journal(db_path: Path, local_date: str, narrative: str, source_json: dict) -> None:
    with connect(db_path) as cx:
        cx.execute(
            """
            INSERT INTO journal_entries (local_date, ts_utc, narrative, source_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(local_date) DO UPDATE SET
              ts_utc = excluded.ts_utc,
              narrative = excluded.narrative,
              source_json = excluded.source_json
            """,
            (local_date, _now_utc_iso(), narrative, json.dumps(source_json, ensure_ascii=False)),
        )


def get_journal(db_path: Path, local_date: str) -> str | None:
    with connect(db_path) as cx:
        row = cx.execute(
            "SELECT narrative FROM journal_entries WHERE local_date = ?", (local_date,)
        ).fetchone()
    return row["narrative"] if row else None


def get_recent_journals(
    db_path: Path, *, end_date: str, days: int = 7
) -> list[dict[str, Any]]:
    """Pull the last `days` journal entries up to and including end_date.

    Returned oldest-first so the weekly Weaver reads them like a chronology.
    Each row carries narrative + the merged source signals so the prompt can
    notice patterns across structured data, not just prose.
    """
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT local_date, narrative, source_json FROM journal_entries "
            "WHERE local_date <= ? "
            "ORDER BY local_date DESC LIMIT ?",
            (end_date, days),
        ).fetchall()
    out = []
    for r in rows:
        try:
            signals = json.loads(r["source_json"]) if r["source_json"] else {}
        except json.JSONDecodeError:
            signals = {}
        out.append({
            "local_date": r["local_date"],
            "narrative": r["narrative"],
            "signals": signals,
        })
    return list(reversed(out))


def upsert_weekly_reflection(
    db_path: Path, *, week_end_date: str, narrative: str
) -> None:
    """Stash the weekly pattern-noticing paragraph so /weave_week can re-read it.

    Stored in journal_entries under a synthetic local_date with a 'week:' prefix
    so the weekly view stays out of the daily journal stream.
    """
    key = f"week:{week_end_date}"
    with connect(db_path) as cx:
        cx.execute(
            """
            INSERT INTO journal_entries (local_date, ts_utc, narrative, source_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(local_date) DO UPDATE SET
              ts_utc = excluded.ts_utc,
              narrative = excluded.narrative,
              source_json = excluded.source_json
            """,
            (key, _now_utc_iso(), narrative, "{}"),
        )


def get_weekly_reflection(db_path: Path, week_end_date: str) -> str | None:
    with connect(db_path) as cx:
        row = cx.execute(
            "SELECT narrative FROM journal_entries WHERE local_date = ?",
            (f"week:{week_end_date}",),
        ).fetchone()
    return row["narrative"] if row else None


def get_latest_weekly_reflection(db_path: Path) -> tuple[str, str] | None:
    """Returns (week_end_date, narrative) for the most recent weekly pass, or None."""
    with connect(db_path) as cx:
        row = cx.execute(
            "SELECT local_date, narrative FROM journal_entries "
            "WHERE local_date LIKE 'week:%' "
            "ORDER BY local_date DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return row["local_date"].removeprefix("week:"), row["narrative"]


# ---------- vibe ----------

def set_vibe(db_path: Path, vibe: str) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "INSERT INTO vibe_state (k,v) VALUES ('current', ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (vibe,),
        )


def get_vibe(db_path: Path, default: str = "smug_construct") -> str:
    with connect(db_path) as cx:
        row = cx.execute("SELECT v FROM vibe_state WHERE k = 'current'").fetchone()
    return row["v"] if row else default


# ---------- v2: status events ----------

def insert_status_event(db_path: Path, *, local_date: str, state: str, note: str | None = None) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "INSERT INTO status_events (ts_utc, local_date, state, note) VALUES (?, ?, ?, ?)",
            (_now_utc_iso(), local_date, state, note),
        )


def get_status_events_for_date(db_path: Path, local_date: str) -> list[dict[str, Any]]:
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT ts_utc, state, note FROM status_events WHERE local_date = ? ORDER BY id ASC",
            (local_date,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- v2: polls ----------

def insert_body_check_poll(
    db_path: Path, *, local_date: str, poll_id: str, options: list[str]
) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "INSERT OR REPLACE INTO body_check_polls (ts_utc, local_date, poll_id, options_json) "
            "VALUES (?, ?, ?, ?)",
            (_now_utc_iso(), local_date, poll_id, json.dumps(options)),
        )


def record_poll_answer(db_path: Path, poll_id: str, selected_indices: list[int]) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "UPDATE body_check_polls SET answers_json = ? WHERE poll_id = ?",
            (json.dumps(selected_indices), poll_id),
        )


def get_polls_for_date(db_path: Path, local_date: str) -> list[dict[str, Any]]:
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT options_json, answers_json FROM body_check_polls WHERE local_date = ? ORDER BY id ASC",
            (local_date,),
        ).fetchall()
    out = []
    for r in rows:
        opts = json.loads(r["options_json"])
        ans = json.loads(r["answers_json"]) if r["answers_json"] else None
        out.append({"options": opts, "selected": [opts[i] for i in (ans or [])]})
    return out


# ---------- v2: rabbit holes ----------

def insert_rabbit_hole(
    db_path: Path, *, local_date: str, raw_text: str, url: str | None, topic: str | None
) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "INSERT INTO rabbit_holes (ts_utc, local_date, url, raw_text, topic) VALUES (?, ?, ?, ?, ?)",
            (_now_utc_iso(), local_date, url, raw_text, topic),
        )


def get_rabbit_holes_for_date(db_path: Path, local_date: str) -> list[dict[str, Any]]:
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT url, raw_text, topic FROM rabbit_holes WHERE local_date = ? ORDER BY id ASC",
            (local_date,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_rabbit_holes_missing_topic(db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch rabbit-hole rows that still need a topic phrase.

    Limited to recent items so a Scribe pass is bounded. Older items left alone.
    """
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT id, url, raw_text FROM rabbit_holes "
            "WHERE topic IS NULL OR topic = '' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_rabbit_hole_topic(db_path: Path, row_id: int, topic: str) -> None:
    with connect(db_path) as cx:
        cx.execute("UPDATE rabbit_holes SET topic = ? WHERE id = ?", (topic, row_id))


# Note: the photo_observations table is left in the schema for back-compat with
# existing databases written before Mind 4 (Eye) was removed. Old rows remain
# queryable via raw SQL; nothing in the codebase reads or writes it now.


# ---------- v3: sleep state ----------

def sleep_state_get(db_path: Path, key: str, default: str | None = None) -> str | None:
    with connect(db_path) as cx:
        row = cx.execute("SELECT v FROM sleep_state WHERE k = ?", (key,)).fetchone()
    return row["v"] if row else default


def sleep_state_set(db_path: Path, key: str, value: str) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "INSERT INTO sleep_state (k,v) VALUES (?,?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )


def sleep_state_delete(db_path: Path, key: str) -> None:
    with connect(db_path) as cx:
        cx.execute("DELETE FROM sleep_state WHERE k = ?", (key,))


# ---------- web-specific helpers ----------


def web_pref_get(db_path: Path, key: str, default: str | None = None) -> str | None:
    with connect(db_path) as cx:
        row = cx.execute("SELECT v FROM web_prefs WHERE k = ?", (key,)).fetchone()
    return row["v"] if row else default


def web_pref_set(db_path: Path, key: str, value: str) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "INSERT INTO web_prefs (k,v) VALUES (?,?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )


def mark_journal_seen(db_path: Path, local_date: str) -> None:
    """Mark a journal entry as seen by the user (clears the unread dot)."""
    with connect(db_path) as cx:
        cx.execute(
            "INSERT INTO journal_seen (local_date, seen_at) VALUES (?,?) "
            "ON CONFLICT(local_date) DO NOTHING",
            (local_date, _now_utc_iso()),
        )


def journal_unread_count(db_path: Path) -> int:
    """How many journal entries exist that haven't been marked seen."""
    with connect(db_path) as cx:
        row = cx.execute(
            "SELECT COUNT(*) AS n FROM journal_entries je "
            "LEFT JOIN journal_seen js USING (local_date) "
            "WHERE js.local_date IS NULL"
        ).fetchone()
    return int(row["n"]) if row else 0


def list_journal_entries(
    db_path: Path, *, limit: int = 30, before_date: str | None = None
) -> list[dict[str, Any]]:
    """Return journal entries newest-first, each tagged with whether seen."""
    with connect(db_path) as cx:
        if before_date:
            rows = cx.execute(
                "SELECT je.local_date, je.ts_utc, je.narrative, "
                "       js.seen_at IS NOT NULL AS seen "
                "FROM journal_entries je "
                "LEFT JOIN journal_seen js USING (local_date) "
                "WHERE je.local_date < ? AND je.local_date NOT LIKE 'week:%' "
                "ORDER BY je.local_date DESC LIMIT ?",
                (before_date, limit),
            ).fetchall()
        else:
            rows = cx.execute(
                "SELECT je.local_date, je.ts_utc, je.narrative, "
                "       js.seen_at IS NOT NULL AS seen "
                "FROM journal_entries je "
                "LEFT JOIN journal_seen js USING (local_date) "
                "WHERE je.local_date NOT LIKE 'week:%' "
                "ORDER BY je.local_date DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {
            "local_date": r["local_date"],
            "ts_utc": r["ts_utc"],
            "narrative": r["narrative"],
            "seen": bool(r["seen"]),
            "kind": "daily",
        }
        for r in rows
    ]


def list_weekly_reflections(
    db_path: Path, *, limit: int = 12
) -> list[dict[str, Any]]:
    """Weekly reflections live in journal_entries with a synthetic 'week:YYYY-MM-DD' key.

    Strip the prefix on the way out. Seen state is tracked the same way daily
    entries are: a row in journal_seen keyed by the synthetic local_date.
    """
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT je.local_date, je.ts_utc, je.narrative, "
            "       js.seen_at IS NOT NULL AS seen "
            "FROM journal_entries je "
            "LEFT JOIN journal_seen js USING (local_date) "
            "WHERE je.local_date LIKE 'week:%' "
            "ORDER BY je.local_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "local_date": r["local_date"].removeprefix("week:"),
            "ts_utc": r["ts_utc"],
            "narrative": r["narrative"],
            "seen": bool(r["seen"]),
            "kind": "weekly",
        }
        for r in rows
    ]


def get_messages_since_id(
    db_path: Path, *, since_id: int = 0, limit: int = 200
) -> list[dict[str, Any]]:
    """Pull messages with id > since_id, newest-last (chat order).

    `since_id=0` returns the most recent `limit` messages in chat order.
    Includes user, assistant, and system_nudge rows (the chat stream surfaces
    Scribe nudges per spec §6).
    """
    with connect(db_path) as cx:
        if since_id > 0:
            rows = cx.execute(
                "SELECT id, ts_utc, role, vibe, content FROM messages "
                "WHERE id > ? AND role IN ('user', 'assistant', 'system_nudge') "
                "ORDER BY id ASC LIMIT ?",
                (since_id, limit),
            ).fetchall()
        else:
            # Return the last `limit` messages, in chronological order
            rows = cx.execute(
                "SELECT id, ts_utc, role, vibe, content FROM ("
                "  SELECT id, ts_utc, role, vibe, content FROM messages "
                "  WHERE role IN ('user', 'assistant', 'system_nudge') "
                "  ORDER BY id DESC LIMIT ?"
                ") ORDER BY id ASC",
                (limit,),
            ).fetchall()
    return [
        {
            "id": r["id"],
            "ts_utc": r["ts_utc"],
            "role": r["role"],
            "vibe": r["vibe"],
            "content": r["content"],
        }
        for r in rows
    ]


def get_message_history_for_persona(
    db_path: Path, *, limit: int = 20
) -> list[dict[str, str]]:
    """Format the last `limit` user/assistant messages for the Persona's chat history.

    Returns OpenAI-style role/content pairs, oldest-first.
    """
    rows = get_messages_since_id(db_path, since_id=0, limit=limit)
    return [{"role": r["role"], "content": r["content"]} for r in rows]


# ---------- dashboard aggregations ----------


def dashboard_message_counts(
    db_path: Path, *, end_date: str, days: int
) -> list[dict[str, Any]]:
    """Daily user-message counts ending at end_date, oldest-first.

    Used both for the sparkline and the streak calculation.
    """
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT local_date, COUNT(*) AS n FROM messages "
            "WHERE role = 'user' AND local_date <= ? "
            "GROUP BY local_date ORDER BY local_date DESC LIMIT ?",
            (end_date, days),
        ).fetchall()
    return [{"local_date": r["local_date"], "count": int(r["n"])} for r in reversed(rows)]


def dashboard_vibe_distribution(
    db_path: Path, *, end_date: str, days: int
) -> list[dict[str, Any]]:
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT vibe, COUNT(*) AS n FROM messages "
            "WHERE role = 'assistant' AND vibe IS NOT NULL "
            "  AND local_date <= ? "
            "  AND local_date >= date(?, '-' || ? || ' days') "
            "GROUP BY vibe ORDER BY n DESC",
            (end_date, end_date, days - 1),
        ).fetchall()
    total = sum(int(r["n"]) for r in rows) or 1
    return [
        {"vibe": r["vibe"], "count": int(r["n"]), "pct": int(r["n"]) / total}
        for r in rows
    ]


def dashboard_hourly_density(
    db_path: Path, *, end_date: str, days: int, tz_name: str
) -> list[dict[str, int]]:
    """Histogram of user messages by hour-of-day in the user's timezone.

    SQLite's time arithmetic doesn't know about IANA timezones, so we read raw
    UTC and convert in Python.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT ts_utc FROM messages "
            "WHERE role = 'user' "
            "  AND local_date <= ? "
            "  AND local_date >= date(?, '-' || ? || ' days')",
            (end_date, end_date, days - 1),
        ).fetchall()
    buckets = [0] * 24
    for r in rows:
        try:
            dt = datetime.fromisoformat(r["ts_utc"].replace("Z", "+00:00"))
            local = dt.astimezone(tz)
            buckets[local.hour] += 1
        except (ValueError, AttributeError):
            continue
    return [{"hour": h, "count": c} for h, c in enumerate(buckets)]


def dashboard_last_message_per_day(
    db_path: Path, *, end_date: str, days: int, tz_name: str
) -> list[dict[str, Any]]:
    """For each of the last `days`, the local time of the last user message that day.

    Used to render the sleep-window adherence row.
    """
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    with connect(db_path) as cx:
        rows = cx.execute(
            "SELECT local_date, MAX(ts_utc) AS last_ts FROM messages "
            "WHERE role = 'user' "
            "  AND local_date <= ? "
            "  AND local_date >= date(?, '-' || ? || ' days') "
            "GROUP BY local_date ORDER BY local_date DESC",
            (end_date, end_date, days - 1),
        ).fetchall()
    out = []
    for r in rows:
        if not r["last_ts"]:
            continue
        try:
            dt = datetime.fromisoformat(r["last_ts"].replace("Z", "+00:00"))
            local = dt.astimezone(tz)
            out.append({
                "local_date": r["local_date"],
                "last_local_hhmm": local.strftime("%H:%M"),
                "last_local_hour": local.hour + local.minute / 60.0,
            })
        except (ValueError, AttributeError):
            continue
    return out


# ---------- auth ----------

def auth_has_passcode(db_path: Path) -> bool:
    with connect(db_path) as cx:
        row = cx.execute("SELECT 1 FROM auth WHERE id = 1").fetchone()
        return row is not None


def auth_set_passcode(db_path: Path, passcode_hash: str) -> None:
    """Insert or replace the single passcode row. Resets lockout state."""
    now = _now_utc_iso()
    with connect(db_path) as cx:
        cx.execute(
            "INSERT INTO auth (id, passcode_hash, created_at, failed_attempts, locked_until) "
            "VALUES (1, ?, ?, 0, NULL) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  passcode_hash = excluded.passcode_hash, "
            "  created_at    = excluded.created_at, "
            "  failed_attempts = 0, "
            "  locked_until  = NULL",
            (passcode_hash, now),
        )


def auth_get_state(db_path: Path) -> dict[str, Any] | None:
    """Returns {'passcode_hash', 'failed_attempts', 'locked_until'} or None."""
    with connect(db_path) as cx:
        row = cx.execute(
            "SELECT passcode_hash, failed_attempts, locked_until FROM auth WHERE id = 1"
        ).fetchone()
        return dict(row) if row else None


def auth_record_failure(db_path: Path, locked_until_iso: str | None) -> int:
    """Bump failed_attempts. If locked_until_iso is given, set the lockout. Returns new attempts count."""
    with connect(db_path) as cx:
        cx.execute(
            "UPDATE auth SET failed_attempts = failed_attempts + 1, locked_until = COALESCE(?, locked_until) "
            "WHERE id = 1",
            (locked_until_iso,),
        )
        row = cx.execute("SELECT failed_attempts FROM auth WHERE id = 1").fetchone()
        return row["failed_attempts"] if row else 0


def auth_clear_failures(db_path: Path) -> None:
    with connect(db_path) as cx:
        cx.execute(
            "UPDATE auth SET failed_attempts = 0, locked_until = NULL WHERE id = 1"
        )


def auth_delete(db_path: Path) -> None:
    """Used by the reset CLI."""
    with connect(db_path) as cx:
        cx.execute("DELETE FROM auth WHERE id = 1")
