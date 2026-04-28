"""Background job scheduler. Runs scribe + weaver + nightly nudge.

Lives in the FastAPI process. Started in the app's lifespan, stopped on
shutdown. Uses APScheduler's AsyncIOScheduler so jobs run on the same loop
as the request handlers and share the same OpenAI AsyncClient.

Jobs that used to send Telegram messages now write into the DB (as
'system_nudge' messages) and the web UI surfaces them on next refresh.
The Weaver still writes journal entries to `journal_entries`; the unread
indicator on /journal is the entire 'notification.'
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from openai import AsyncOpenAI

from . import db, nudges, time_ctx
from .config import Config
from .minds import scribe as mind2
from .minds import weaver as mind3
from .personas.catalog import DEFAULT_VIBE


log = logging.getLogger("meatsuit.scheduler")


# Sleep-state keys (carried over from the bot, same semantics).
_SS_OFF_FOREVER = "off_forever"
_SS_SNOOZE_DATE = "snooze_date"
_SS_LAST_NUDGE_DATE = "last_nudge_date"


def _today_local(cfg: Config) -> str:
    return datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")


# ---------- job bodies ----------


async def run_scribe_pass(cfg: Config, client: AsyncOpenAI) -> None:
    """Mind 2: pull unscribed messages for today, extract JSON, mark them.

    Also backfills rabbit-hole topics (cheap per-item call, capped per pass).
    """
    local_date = _today_local(cfg)

    rows = db.get_unscribed_for_date(cfg.database_path, local_date)
    if rows:
        transcript_lines = [f"[{r['role']}] {r['content']}" for r in rows]
        log.info("scribe: extracting from %d new lines on %s", len(rows), local_date)
        try:
            payload = await mind2.extract(
                client, model=cfg.mind2_model, transcript_lines=transcript_lines,
            )
            db.insert_extraction(cfg.database_path, local_date, payload)
            db.mark_scribed(cfg.database_path, [int(r["id"]) for r in rows])
        except Exception:
            log.exception("Mind 2 failure")
    else:
        log.debug("scribe: nothing new for %s", local_date)

    pending = db.get_rabbit_holes_missing_topic(cfg.database_path, limit=20)
    if pending:
        log.info("scribe: enriching %d rabbit-hole topics", len(pending))
        for item in pending:
            try:
                topic = await mind2.topic_for(
                    client, model=cfg.mind2_model,
                    raw_text=item["raw_text"], url=item["url"],
                )
                db.set_rabbit_hole_topic(cfg.database_path, item["id"], topic)
            except Exception:
                log.exception("rabbit-hole topic enrichment failed for id=%s", item["id"])


async def run_weaver(cfg: Config, client: AsyncOpenAI) -> None:
    """Mind 3 (daily): write a first-person journal entry for today.

    Stores the entry in `journal_entries`. No external delivery — the web app
    discovers it on next /journal load and the unread dot lights up.
    """
    local_date = _today_local(cfg)

    chat_signals = mind2.merge_payloads(
        db.get_extractions_for_date(cfg.database_path, local_date)
    )
    status_taps = db.get_status_events_for_date(cfg.database_path, local_date)
    polls = db.get_polls_for_date(cfg.database_path, local_date)
    rabbits = db.get_rabbit_holes_for_date(cfg.database_path, local_date)

    has_any_signal = (
        any(chat_signals.get("gratitudes") or [])
        or chat_signals.get("mental_weather")
        or any(
            v.get("status", "unknown") != "unknown"
            for v in chat_signals.get("meat_suit_maintenance", {}).values()
        )
        or status_taps
        or polls
        or rabbits
    )
    if not has_any_signal:
        log.info("weaver: no signals for %s, skipping", local_date)
        return

    bundle = {
        "chat_signals": chat_signals,
        "status_taps": status_taps,
        "body_check_poll": polls[-1] if polls else None,
        "rabbit_holes": rabbits,
    }
    voice_samples = db.get_user_voice_samples(cfg.database_path, limit=12)

    timing_rows = db.get_message_timing_window(cfg.database_path, limit=200)
    user_ts_today = [row["ts_utc"] for row in timing_rows if row["role"] == "user"]
    rhythm_note = time_ctx.rhythm_summary(user_ts_today, timezone=cfg.timezone) or None

    try:
        narrative = await mind3.weave(
            client,
            model=cfg.mind3_model,
            merged_payload=bundle,
            local_date=local_date,
            voice_samples=voice_samples,
            rhythm_note=rhythm_note,
        )
        db.upsert_journal(cfg.database_path, local_date, narrative, bundle)
        log.info("weaver: wrote journal for %s (%d chars)", local_date, len(narrative))
    except Exception:
        log.exception("Mind 3 failure")


async def run_weekly_weaver(cfg: Config, client: AsyncOpenAI) -> None:
    """Mind 3 (weekly): Sunday-night pattern-noticer. Stash-only, surfaced via /api/journal."""
    today = _today_local(cfg)
    daily = db.get_recent_journals(cfg.database_path, end_date=today, days=7)
    if len(daily) < 2:
        log.info("weekly: only %d daily entries, skipping", len(daily))
        return

    voice_samples = db.get_user_voice_samples(cfg.database_path, limit=16)
    late_dates = time_ctx.late_night_days(daily, timezone=cfg.timezone)
    try:
        narrative = await mind3.weave_week(
            client,
            model=cfg.mind3_model,
            week_end_date=today,
            daily_entries=daily,
            voice_samples=voice_samples,
            late_night_dates=late_dates,
        )
    except Exception:
        log.exception("weekly weaver failure")
        return

    if not narrative:
        return

    db.upsert_weekly_reflection(
        cfg.database_path, week_end_date=today, narrative=narrative,
    )
    log.info("weekly: wrote reflection for week ending %s", today)


async def run_anti_pda_nudge(cfg: Config) -> None:
    """The 11:00 / 17:30 sideways observation. Writes to chat as 'assistant.'"""
    if not cfg.nudges_enabled:
        return
    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)
    hour = datetime.now(ZoneInfo(cfg.timezone)).hour
    text = nudges.pick(vibe_key, hour=hour)
    db.insert_message(
        cfg.database_path,
        local_date=_today_local(cfg),
        role="assistant",
        vibe=vibe_key,
        content=text,
    )
    log.info("anti-pda nudge fired (vibe=%s, hour=%d)", vibe_key, hour)


async def run_sleep_nudge(cfg: Config) -> None:
    """Nightly cutoff nudge. Same gating as the old Telegram version."""
    if not cfg.sleep_nudges_enabled:
        return
    today = _today_local(cfg)

    if db.sleep_state_get(cfg.database_path, _SS_OFF_FOREVER) == "1":
        return
    if db.sleep_state_get(cfg.database_path, _SS_SNOOZE_DATE) == today:
        return
    if db.sleep_state_get(cfg.database_path, _SS_LAST_NUDGE_DATE) == today:
        log.info("sleep nudge: already fired today, skipping")
        return

    tc = time_ctx.now_context(
        timezone=cfg.timezone,
        weeknight_cutoff_hour=cfg.sleep_weeknight_hour,
        weekend_cutoff_hour=cfg.sleep_weekend_hour,
    )
    if not tc.sleep_window_active:
        return

    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)
    msg = nudges.pick_sleep(vibe_key)

    db.insert_message(
        cfg.database_path,
        local_date=today,
        role="system_nudge",
        vibe=vibe_key,
        content=msg,
    )
    db.sleep_state_set(cfg.database_path, _SS_LAST_NUDGE_DATE, today)
    log.info("sleep nudge fired for %s", today)


# ---------- scheduler wiring ----------


def build_scheduler(cfg: Config, client: AsyncOpenAI) -> AsyncIOScheduler:
    """Construct (but do not start) the scheduler with all jobs registered.

    Caller (the FastAPI lifespan) is responsible for .start() and .shutdown().
    Each job is wrapped in a tiny lambda that captures cfg + client so the
    job functions stay testable on their own.
    """
    tz = ZoneInfo(cfg.timezone)
    sched = AsyncIOScheduler(timezone=tz)

    # Scribe: every 30 minutes.
    sched.add_job(
        run_scribe_pass, IntervalTrigger(minutes=30, timezone=tz),
        kwargs={"cfg": cfg, "client": client},
        id="scribe", replace_existing=True,
    )

    # Weaver: 23:59 local. Run a final scribe pass first so it has fresh extractions.
    async def _nightly() -> None:
        await run_scribe_pass(cfg, client)
        await run_weaver(cfg, client)

    sched.add_job(
        _nightly, CronTrigger(hour=23, minute=59, timezone=tz),
        id="weaver_nightly", replace_existing=True,
    )

    # Weekly weaver: Sunday 22:00 local.
    sched.add_job(
        run_weekly_weaver, CronTrigger(day_of_week="sun", hour=22, minute=0, timezone=tz),
        kwargs={"cfg": cfg, "client": client},
        id="weaver_weekly", replace_existing=True,
    )

    # Anti-PDA nudges: 11:00 and 17:30.
    if cfg.nudges_enabled:
        for hour, minute in [(11, 0), (17, 30)]:
            sched.add_job(
                run_anti_pda_nudge, CronTrigger(hour=hour, minute=minute, timezone=tz),
                kwargs={"cfg": cfg},
                id=f"nudge_{hour:02d}{minute:02d}", replace_existing=True,
            )

    # Sleep nudges: at each configured cutoff hour. The job re-checks state.
    if cfg.sleep_nudges_enabled:
        for hour in {cfg.sleep_weeknight_hour, cfg.sleep_weekend_hour}:
            sched.add_job(
                run_sleep_nudge, CronTrigger(hour=hour, minute=0, timezone=tz),
                kwargs={"cfg": cfg},
                id=f"sleep_{hour:02d}", replace_existing=True,
            )

    return sched
