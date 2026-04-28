"""FastAPI app — single-process backend for the Meat-Suit web UI.

Serves `/api/*` and (in production) the built React SPA at `/`. The brain's
APScheduler runs in this same event loop via the lifespan context manager, so
the Scribe / Weaver / nudge jobs fire from inside the same process that's
answering HTTP requests. One DB, one event loop, one thing to start.

Auth: passcode-based. On first run the user picks a 6-digit passcode at
/setup; it's stored in SQLite as an argon2id hash. Subsequent visits POST the
passcode to /api/auth/login and get a signed httpOnly session cookie back. The
cookie is signed with `MEAT_SUIT_COOKIE_SECRET` (auto-generated to .env, never
typed). Five wrong attempts triggers a 15-minute global lockout. Forgot the
passcode? Run `python -m meat_suit.reset_passcode`.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from datetime import timezone as _tz
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from . import db, nudges, time_ctx
from .config import Config, load_config
from .minds import persona as mind1
from .minds import scribe as mind2
from .personas.catalog import CATALOG, DEFAULT_VIBE, list_personas
from .scheduler import build_scheduler


log = logging.getLogger("meatsuit.api")


# ---------- auth ----------

_COOKIE_NAME = "meat_suit_session"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days, refreshed on use
_SESSION_SALT = "meat-suit-session-v1"

# Lockout policy. Global (single-tenant), simple, safe.
_MAX_FAILED_ATTEMPTS = 5
_LOCKOUT_MINUTES = 15

# Passcode shape. 6 digits is the recommended default (see Apr 2026 design call).
_PASSCODE_MIN_LEN = 4
_PASSCODE_MAX_LEN = 12

_password_hasher = PasswordHasher()


def _serializer(cfg: Config) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(cfg.cookie_secret, salt=_SESSION_SALT)


def _sign_session(cfg: Config) -> str:
    """Mint the cookie value. The payload is a fixed marker; the signature is
    the actual proof. Rotating cookie_secret invalidates all sessions."""
    return _serializer(cfg).dumps({"v": 1})


def _verify_session(cfg: Config, raw: str | None) -> bool:
    if not raw:
        return False
    try:
        _serializer(cfg).loads(raw, max_age=_COOKIE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _set_session_cookie(resp: Response, cfg: Config) -> None:
    resp.set_cookie(
        key=_COOKIE_NAME,
        value=_sign_session(cfg),
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=cfg.web_cookie_secure,
        samesite="lax",
        path="/",
    )


def _parse_iso_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_locked(auth_state: dict[str, Any]) -> tuple[bool, datetime | None]:
    """Returns (locked, locked_until_dt). The lockout self-expires; we just
    check the timestamp instead of running a cleanup job."""
    locked_until = _parse_iso_utc(auth_state.get("locked_until"))
    if locked_until and locked_until > datetime.now(_tz.utc):
        return True, locked_until
    return False, locked_until


def require_session(
    request: Request,
    session: str | None = Cookie(None, alias=_COOKIE_NAME),
) -> None:
    """Dependency injected into every protected route."""
    cfg: Config = request.app.state.cfg
    if not _verify_session(cfg, session):
        raise HTTPException(status_code=401, detail="unauthorized")


# ---------- request/response models ----------


class VibeIn(BaseModel):
    vibe: str = Field(..., description="persona key, e.g. 'feral_bff'")


class MessageIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=8000)
    vibe: str | None = Field(None, description="optional override for this turn")


class SettingsIn(BaseModel):
    vibe: str | None = None
    sleep_weeknight_hour: int | None = Field(None, ge=0, le=23)
    sleep_weekend_hour: int | None = Field(None, ge=0, le=23)
    sleep_nudges_enabled: bool | None = None
    nudges_enabled: bool | None = None


# ---------- helpers ----------


def _today_local(cfg: Config) -> str:
    return datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")


def _persona_payload() -> list[dict[str, str]]:
    return [{"key": p.key, "display": p.display} for p in list_personas()]


def _settings_payload(cfg: Config) -> dict[str, Any]:
    """Settings are a mix of DB-backed (vibe, sleep_state) and config-backed.

    The config-backed values can't actually be patched at runtime in v1 (they
    come from .env), but we expose them read-only here so the UI can render
    the current rhythm. The web_prefs table holds anything we want mutable
    later without touching the .env file.
    """
    vibe = db.get_vibe(cfg.database_path, DEFAULT_VIBE)
    sleep_off_forever = db.sleep_state_get(cfg.database_path, "off_forever") == "1"
    nudges_off = db.web_pref_get(cfg.database_path, "nudges_off") == "1"
    return {
        "vibe": vibe,
        "personas": _persona_payload(),
        "rhythm": {
            "sleep_weeknight_hour": cfg.sleep_weeknight_hour,
            "sleep_weekend_hour": cfg.sleep_weekend_hour,
            "sleep_nudges_enabled": cfg.sleep_nudges_enabled and not sleep_off_forever,
            "nudges_enabled": cfg.nudges_enabled and not nudges_off,
            "timezone": cfg.timezone,
        },
    }


# ---------- lifespan ----------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load config, init DB, build the AsyncOpenAI client, start the scheduler.

    The scheduler runs on the same loop, so its jobs share this client. On
    shutdown we drain APScheduler then close the HTTP client.
    """
    cfg = load_config()
    db.init_db(cfg.database_path)

    client = AsyncOpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url)
    scheduler = build_scheduler(cfg, client)
    scheduler.start()

    app.state.cfg = cfg
    app.state.openai = client
    app.state.scheduler = scheduler

    log.info(
        "meat-suit api up: bind=%s:%d tz=%s db=%s",
        cfg.web_bind_host, cfg.web_port, cfg.timezone, cfg.database_path,
    )
    if not db.auth_has_passcode(cfg.database_path):
        log.info(
            "first-run: no passcode set. open http://%s:%d/ to pick one.",
            cfg.web_bind_host, cfg.web_port,
        )
    else:
        log.info(
            "open http://%s:%d/ to log in with your passcode.",
            cfg.web_bind_host, cfg.web_port,
        )

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await client.close()


# ---------- app ----------


app = FastAPI(lifespan=lifespan, title="Meat-Suit", docs_url=None, redoc_url=None)

# CORS only matters during dev when Vite runs on a different port. In prod
# FastAPI serves the SPA from the same origin. Allow localhost ports broadly
# during dev; tighten if/when this ever runs cross-origin in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5050", "http://127.0.0.1:5050",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail if isinstance(exc.detail, str) else "error"},
    )


# ---------- auth routes ----------


class PasscodeIn(BaseModel):
    passcode: str = Field(..., min_length=_PASSCODE_MIN_LEN, max_length=_PASSCODE_MAX_LEN)


def _validate_passcode_shape(passcode: str) -> None:
    """Server-side guardrail. We accept 4-12 chars, all numeric. The UI
    defaults to 6 digits; this just keeps the door from being a single space."""
    if not passcode.isdigit():
        raise HTTPException(
            status_code=400, detail="passcode must be digits only"
        )


@app.get("/api/auth/status")
async def auth_status(
    request: Request,
    session: str | None = Cookie(None, alias=_COOKIE_NAME),
) -> dict[str, Any]:
    """Public — the SPA hits this on boot to decide which screen to show."""
    cfg: Config = request.app.state.cfg
    state = db.auth_get_state(cfg.database_path)
    has_passcode = state is not None
    locked = False
    locked_until_iso: str | None = None
    if state:
        is_locked, locked_until = _is_locked(state)
        locked = is_locked
        locked_until_iso = locked_until.isoformat() if locked_until and is_locked else None
    return {
        "has_passcode": has_passcode,
        "authenticated": _verify_session(cfg, session),
        "locked": locked,
        "locked_until": locked_until_iso,
    }


@app.post("/api/auth/setup")
async def auth_setup(request: Request, body: PasscodeIn) -> Response:
    """First-time passcode setup. Refuses once a passcode exists; in that case
    use the reset CLI to clear it."""
    cfg: Config = request.app.state.cfg
    if db.auth_has_passcode(cfg.database_path):
        raise HTTPException(
            status_code=409,
            detail="passcode already set; use reset CLI to clear it",
        )
    _validate_passcode_shape(body.passcode)
    h = _password_hasher.hash(body.passcode)
    db.auth_set_passcode(cfg.database_path, h)
    resp = JSONResponse({"ok": True})
    _set_session_cookie(resp, cfg)
    return resp


@app.post("/api/auth/login")
async def auth_login(request: Request, body: PasscodeIn) -> Response:
    cfg: Config = request.app.state.cfg
    state = db.auth_get_state(cfg.database_path)
    if not state:
        # No passcode set yet -> direct user to /setup. 409 is a clearer signal
        # than 401 for "this server has no auth at all yet."
        raise HTTPException(status_code=409, detail="no passcode set; visit /setup")

    is_locked, locked_until = _is_locked(state)
    if is_locked:
        raise HTTPException(
            status_code=429,
            detail=f"locked until {locked_until.isoformat() if locked_until else 'soon'}",
        )

    try:
        _password_hasher.verify(state["passcode_hash"], body.passcode)
    except VerifyMismatchError:
        new_attempts = state["failed_attempts"] + 1
        lock_iso: str | None = None
        if new_attempts >= _MAX_FAILED_ATTEMPTS:
            lock_until = datetime.now(_tz.utc) + timedelta(minutes=_LOCKOUT_MINUTES)
            lock_iso = lock_until.isoformat()
        actual_attempts = db.auth_record_failure(cfg.database_path, lock_iso)
        remaining = max(0, _MAX_FAILED_ATTEMPTS - actual_attempts)
        if lock_iso:
            raise HTTPException(
                status_code=429,
                detail=f"too many tries; locked for {_LOCKOUT_MINUTES} minutes",
            )
        raise HTTPException(
            status_code=401,
            detail=f"wrong passcode; {remaining} tries left",
        )

    db.auth_clear_failures(cfg.database_path)
    resp = JSONResponse({"ok": True})
    _set_session_cookie(resp, cfg)
    return resp


@app.post("/api/auth/logout")
async def auth_logout() -> Response:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_COOKIE_NAME, path="/")
    return resp


# ---------- protected routes ----------


@app.get("/api/state", dependencies=[Depends(require_session)])
async def get_state(request: Request) -> dict[str, Any]:
    cfg: Config = request.app.state.cfg
    last = db.get_messages_since_id(cfg.database_path, since_id=0, limit=1)
    return {
        "vibe": db.get_vibe(cfg.database_path, DEFAULT_VIBE),
        "personas": _persona_payload(),
        "unread_journal": db.journal_unread_count(cfg.database_path),
        "last_message_id": last[-1]["id"] if last else 0,
        "first_run": not db.web_pref_get(cfg.database_path, "personas_seen"),
    }


@app.get("/api/personas", dependencies=[Depends(require_session)])
async def get_personas() -> dict[str, Any]:
    return {"personas": _persona_payload(), "default": DEFAULT_VIBE}


@app.post("/api/personas/seen", dependencies=[Depends(require_session)])
async def mark_personas_seen(request: Request) -> dict[str, bool]:
    cfg: Config = request.app.state.cfg
    db.web_pref_set(cfg.database_path, "personas_seen", "1")
    return {"ok": True}


@app.post("/api/vibe", dependencies=[Depends(require_session)])
async def post_vibe(body: VibeIn, request: Request) -> dict[str, str]:
    cfg: Config = request.app.state.cfg
    if body.vibe not in CATALOG:
        raise HTTPException(status_code=400, detail="unknown vibe")
    db.set_vibe(cfg.database_path, body.vibe)
    return {"vibe": body.vibe}


@app.get("/api/messages", dependencies=[Depends(require_session)])
async def get_messages(
    request: Request,
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    cfg: Config = request.app.state.cfg
    rows = db.get_messages_since_id(cfg.database_path, since_id=since_id, limit=limit)
    return {"messages": rows}


@app.post("/api/messages", dependencies=[Depends(require_session)])
async def post_message(body: MessageIn, request: Request) -> dict[str, Any]:
    """Synchronous reply path. Insert the user row, run Mind 1, insert the
    assistant row, return both. Mid-thread persona switch is handled by
    setting the vibe before the call (the inline `— switched to —` marker is
    a frontend concern; on the server, we just record `vibe` on the row).
    """
    cfg: Config = request.app.state.cfg
    client: AsyncOpenAI = request.app.state.openai
    text = body.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty message")

    # Honor a per-turn vibe override; persist it as the new active vibe so the
    # switcher state stays consistent. If unset, use whatever's stored.
    if body.vibe and body.vibe in CATALOG:
        db.set_vibe(cfg.database_path, body.vibe)
    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)

    local_date = _today_local(cfg)

    # Build context notes from history BEFORE inserting the new user row.
    timing_window = db.get_message_timing_window(cfg.database_path, limit=50)
    msg_records = [
        time_ctx.MessageRecord(ts_utc_iso=row["ts_utc"], role=row["role"])
        for row in timing_window
    ]
    now_utc = datetime.now(_tz.utc)
    mc = time_ctx.build_message_context(now_utc=now_utc, history=msg_records)
    message_ctx_note = time_ctx.format_message_context_for_persona(mc) or None

    tc = time_ctx.now_context(
        timezone=cfg.timezone,
        weeknight_cutoff_hour=cfg.sleep_weeknight_hour,
        weekend_cutoff_hour=cfg.sleep_weekend_hour,
    )
    time_ctx_note = time_ctx.format_for_persona(tc)

    user_id = db.insert_message(
        cfg.database_path, local_date=local_date,
        role="user", vibe=vibe_key, content=text,
    )

    history_rows = db.get_recent_messages(cfg.database_path, limit=20)
    # Drop the just-inserted user row from the history we feed back as
    # context; it's the explicit `user_message` arg.
    history = [{"role": r["role"], "content": r["content"]} for r in history_rows[:-1]]
    voice_samples = db.get_user_voice_samples(cfg.database_path, limit=8)

    try:
        reply = await mind1.reply(
            client, model=cfg.mind1_model, vibe_key=vibe_key,
            history=history, user_message=text,
            voice_samples=voice_samples,
            time_ctx_note=time_ctx_note,
            message_ctx_note=message_ctx_note,
        )
    except Exception:
        log.exception("Mind 1 failure")
        reply = "lost the thread for a sec. say it again?"

    asst_id = db.insert_message(
        cfg.database_path, local_date=local_date,
        role="assistant", vibe=vibe_key, content=reply,
    )

    return {
        "user": {"id": user_id, "role": "user", "vibe": vibe_key, "content": text},
        "assistant": {"id": asst_id, "role": "assistant", "vibe": vibe_key, "content": reply},
    }


@app.delete("/api/messages", dependencies=[Depends(require_session)])
async def delete_messages(request: Request) -> dict[str, bool]:
    """Reset chat history. Leaves extractions and journals intact on purpose:
    the journal is a separate artifact, and the Scribe's daily JSON merges
    are already committed even if the source rows go away.
    """
    cfg: Config = request.app.state.cfg
    with db.connect(cfg.database_path) as cx:
        cx.execute("DELETE FROM messages")
    return {"ok": True}


@app.get("/api/journal", dependencies=[Depends(require_session)])
async def get_journal(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
    before: str | None = Query(None, description="YYYY-MM-DD"),
    kind: str = Query("all", pattern="^(all|daily|weekly)$"),
) -> dict[str, Any]:
    cfg: Config = request.app.state.cfg
    daily = db.list_journal_entries(cfg.database_path, limit=limit, before_date=before)
    weekly = db.list_weekly_reflections(cfg.database_path, limit=12) if kind != "daily" else []
    if kind == "weekly":
        entries = weekly
    elif kind == "daily":
        entries = daily
    else:
        entries = sorted(
            [*daily, *weekly], key=lambda e: e["local_date"], reverse=True,
        )[:limit]
    return {"entries": entries, "unread": db.journal_unread_count(cfg.database_path)}


@app.post("/api/journal/{local_date}/seen", dependencies=[Depends(require_session)])
async def post_journal_seen(
    local_date: str,
    request: Request,
    kind: str = Query("daily", pattern="^(daily|weekly)$"),
) -> dict[str, int]:
    cfg: Config = request.app.state.cfg
    # weekly entries live under a synthetic 'week:YYYY-MM-DD' key in journal_entries;
    # the frontend sends the bare week-end date and we re-prefix here.
    key = f"week:{local_date}" if kind == "weekly" else local_date
    db.mark_journal_seen(cfg.database_path, key)
    return {"unread": db.journal_unread_count(cfg.database_path)}


@app.get("/api/data/summary", dependencies=[Depends(require_session)])
async def get_data_summary(
    request: Request,
    range: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
) -> dict[str, Any]:
    cfg: Config = request.app.state.cfg
    end_date = _today_local(cfg)
    days = {"7d": 7, "30d": 30, "90d": 90, "all": 365 * 5}[range]

    counts = db.dashboard_message_counts(cfg.database_path, end_date=end_date, days=days)
    sparkline = [c["count"] for c in counts]
    messages_total = sum(sparkline)

    # Streak: number of trailing consecutive days with at least one user message.
    streak = 0
    for c in reversed(counts):
        if c["count"] > 0:
            streak += 1
        else:
            break

    last_per_day = db.dashboard_last_message_per_day(
        cfg.database_path, end_date=end_date, days=days, tz_name=cfg.timezone,
    )
    weeknight_cut = cfg.sleep_weeknight_hour
    weekend_cut = cfg.sleep_weekend_hour

    def _adherence_status(date_str: str, last_hour: float) -> str:
        # The cutoff hour is the start of the sleep window (e.g. 23 means "in
        # bed by 11pm"). Anything after midnight wraps to a small_hours number;
        # we treat 0–6am as "after the cutoff."
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return "unknown"
        is_weekend = d.weekday() >= 4  # Fri/Sat nights count as weekend
        cutoff = weekend_cut if is_weekend else weeknight_cut
        # Hours past the cutoff, accounting for after-midnight wrap.
        if last_hour >= cutoff:
            late = last_hour - cutoff
        elif last_hour < 6:
            late = (24 - cutoff) + last_hour
        else:
            return "within"
        if late <= 1.0:
            return "late"
        return "very_late"

    sleep_adherence = [
        {
            "date": row["local_date"],
            "last_msg_local": row["last_local_hhmm"],
            "status": _adherence_status(row["local_date"], row["last_local_hour"]),
        }
        for row in last_per_day
    ]

    after_11pm = sum(
        1 for r in last_per_day
        if r["last_local_hour"] >= 23 or r["last_local_hour"] < 5
    )
    after_11pm_pct = (after_11pm / len(last_per_day)) if last_per_day else 0.0

    vibes = db.dashboard_vibe_distribution(
        cfg.database_path, end_date=end_date, days=days,
    )
    vibe_distribution = [
        {
            "key": v["vibe"],
            "display": CATALOG[v["vibe"]].display if v["vibe"] in CATALOG else v["vibe"],
            "count": v["count"],
            "pct": v["pct"],
        }
        for v in vibes
    ]

    hourly = db.dashboard_hourly_density(
        cfg.database_path, end_date=end_date, days=days, tz_name=cfg.timezone,
    )

    # Weekly comparison: this week vs the prior week.
    this_week = db.dashboard_message_counts(cfg.database_path, end_date=end_date, days=7)
    prev_end = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    last_week = db.dashboard_message_counts(cfg.database_path, end_date=prev_end, days=7)

    def _top_vibe(start_date: str, length: int) -> str | None:
        d = db.dashboard_vibe_distribution(
            cfg.database_path, end_date=start_date, days=length,
        )
        return d[0]["vibe"] if d else None

    return {
        "range": range,
        "kpis": {
            "messages_total": messages_total,
            "messages_sparkline": sparkline,
            "streak_days": streak,
            "after_11pm_pct": round(after_11pm_pct, 3),
        },
        "vibe_distribution": vibe_distribution,
        "sleep_adherence": sleep_adherence,
        "hourly_density": hourly,
        "weekly_compare": {
            "this_week": {
                "messages": sum(c["count"] for c in this_week),
                "late_nights": sum(
                    1 for r in last_per_day[-7:]
                    if _adherence_status(r["local_date"], r["last_local_hour"]) != "within"
                ),
                "top_vibe": _top_vibe(end_date, 7),
            },
            "last_week": {
                "messages": sum(c["count"] for c in last_week),
                "late_nights": sum(
                    1 for r in last_per_day[-14:-7]
                    if _adherence_status(r["local_date"], r["last_local_hour"]) != "within"
                ),
                "top_vibe": _top_vibe(prev_end, 7),
            },
        },
    }


@app.get("/api/settings", dependencies=[Depends(require_session)])
async def get_settings(request: Request) -> dict[str, Any]:
    return _settings_payload(request.app.state.cfg)


@app.patch("/api/settings", dependencies=[Depends(require_session)])
async def patch_settings(body: SettingsIn, request: Request) -> dict[str, Any]:
    """Vibe is the only setting we can change at runtime in v1. Sleep windows
    and nudge cadences come from .env and require a process restart to take
    effect; we accept those fields for forward-compat but no-op on them with
    a `restart_required` flag in the response.
    """
    cfg: Config = request.app.state.cfg
    restart_required: list[str] = []
    if body.vibe:
        if body.vibe not in CATALOG:
            raise HTTPException(status_code=400, detail="unknown vibe")
        db.set_vibe(cfg.database_path, body.vibe)
    if body.sleep_weeknight_hour is not None and body.sleep_weeknight_hour != cfg.sleep_weeknight_hour:
        db.web_pref_set(cfg.database_path, "pending_sleep_weeknight_hour", str(body.sleep_weeknight_hour))
        restart_required.append("sleep_weeknight_hour")
    if body.sleep_weekend_hour is not None and body.sleep_weekend_hour != cfg.sleep_weekend_hour:
        db.web_pref_set(cfg.database_path, "pending_sleep_weekend_hour", str(body.sleep_weekend_hour))
        restart_required.append("sleep_weekend_hour")
    if body.nudges_enabled is False:
        db.web_pref_set(cfg.database_path, "nudges_off", "1")
    elif body.nudges_enabled is True:
        db.web_pref_set(cfg.database_path, "nudges_off", "0")
    if body.sleep_nudges_enabled is False:
        db.sleep_state_set(cfg.database_path, "off_forever", "1")
    elif body.sleep_nudges_enabled is True:
        db.sleep_state_set(cfg.database_path, "off_forever", "0")
    return {**_settings_payload(cfg), "restart_required": restart_required}


@app.post("/api/sleep/snooze", dependencies=[Depends(require_session)])
async def post_sleep_snooze(request: Request) -> dict[str, str]:
    cfg: Config = request.app.state.cfg
    today = _today_local(cfg)
    db.sleep_state_set(cfg.database_path, "snooze_date", today)
    return {"snoozed_until": today}


@app.post("/api/sleep/off-forever", dependencies=[Depends(require_session)])
async def post_sleep_off_forever(request: Request) -> dict[str, bool]:
    cfg: Config = request.app.state.cfg
    db.sleep_state_set(cfg.database_path, "off_forever", "1")
    return {"off_forever": True}


@app.post("/api/check", dependencies=[Depends(require_session)])
async def post_check(request: Request) -> dict[str, Any]:
    """On-demand status nudge composed in the active voice. Writes a
    `system_nudge` row so it shows up in the chat stream on next refresh.
    """
    cfg: Config = request.app.state.cfg
    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)
    hour = datetime.now(ZoneInfo(cfg.timezone)).hour
    text = nudges.pick(vibe_key, hour=hour)
    msg_id = db.insert_message(
        cfg.database_path, local_date=_today_local(cfg),
        role="system_nudge", vibe=vibe_key, content=text,
    )
    return {"id": msg_id, "vibe": vibe_key, "content": text}


@app.get("/api/export", dependencies=[Depends(require_session)])
async def get_export(request: Request) -> Response:
    """Full data export — every table the brain owns, as one JSON document.

    The point is portability and audit, not a tidy backup format. The download
    filename includes the local date so multiple exports stay distinguishable.
    """
    cfg: Config = request.app.state.cfg
    today = _today_local(cfg)
    with db.connect(cfg.database_path) as cx:
        bundle: dict[str, Any] = {
            "exported_at_utc": datetime.now(_tz.utc).isoformat(),
            "timezone": cfg.timezone,
            "tables": {},
        }
        for table in (
            "messages", "extractions", "journal_entries",
            "vibe_state", "status_events", "body_check_polls",
            "rabbit_holes", "photo_observations", "sleep_state",
            "journal_seen", "web_prefs",
        ):
            try:
                rows = cx.execute(f"SELECT * FROM {table}").fetchall()
                bundle["tables"][table] = [dict(r) for r in rows]
            except Exception:
                # Schema drift: skip missing tables silently rather than fail
                # the whole export.
                bundle["tables"][table] = []
    payload = json.dumps(bundle, indent=2, ensure_ascii=False)
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="meat_suit_export_{today}.json"',
        },
    )


# ---------- static SPA mount ----------


# In production, FastAPI serves the built React bundle at `/`. The path is
# resolved at import-time from this module's location so the app works
# regardless of cwd. If the directory doesn't exist (dev mode, Vite running
# separately on :5173), we skip the mount and the SPA is reached over Vite's
# own server with `/api/*` proxied to us.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPA_DIST = _REPO_ROOT / "web" / "dist" / "public"

if _SPA_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_SPA_DIST), html=True), name="spa")
else:
    @app.get("/")
    async def _spa_missing() -> dict[str, str]:
        return {
            "error": "SPA build not found",
            "expected_at": str(_SPA_DIST),
            "hint": "run `cd web && npm run build`, or run Vite dev server on :5173.",
        }
