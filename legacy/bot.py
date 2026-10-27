"""Telegram bot — front door for the Persona, plus visual/haptic intake & cron."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time as dtime, timedelta, timezone as _tz
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI
from telegram import (
    KeyboardButton,
    PollAnswer,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

from . import body_check, db, low_spoon, nudges, time_ctx
from .config import Config, load_config
from .minds import persona as mind1
from .minds import scribe as mind2
from .minds import weaver as mind3
from .personas.catalog import CATALOG, DEFAULT_VIBE, list_personas

log = logging.getLogger("meatsuit")

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Input caps. The user is the only one talking to the bot, but defensive
# programming still beats blowing up on a giant paste or a 50MB photo.
MAX_TEXT_CHARS = 8000          # ~2000 tokens; enough for any human paste

# Sleep-state keys used in the sleep_state k/v table.
_SS_OFF_FOREVER = "off_forever"
_SS_SNOOZE_DATE = "snooze_date"        # YYYY-MM-DD; if set to today, no nudge tonight
_SS_LAST_NUDGE_DATE = "last_nudge_date"


def _redact(text: str | None, n: int = 200) -> str:
    """For log lines: trim user content so a verbose paste doesn't fill logs."""
    if not text:
        return ""
    s = text.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


# ----------------------------- helpers -----------------------------

def _today_local(cfg: Config) -> str:
    return datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")


def _is_owner(cfg: Config, update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == cfg.telegram_owner_id)


def _spoon_keyboard() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(label) for label in row] for row in low_spoon.KEYBOARD_LAYOUT]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def _friendly_date_label(today, days_ago: int) -> str:
    """Soft date label for journal delivery — 'today', 'last night', 'two nights ago'."""
    if days_ago == 0:
        return "today, the way it read:"
    if days_ago == 1:
        return "last night:"
    words = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}
    return f"{words.get(days_ago, str(days_ago))} nights ago:"


# ----------------------------- commands -----------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update):
        return
    persona = CATALOG[db.get_vibe(cfg.database_path, DEFAULT_VIBE)]
    await update.message.reply_text(
        f"here. {persona.display.lower()} today.\n"
        "/vibe to switch · /journal for last night's · /weave_now if you want it now\n"
        "/week for the sunday-night look-back · /check if you want the body poll\n"
        "/menu off if the buttons are too much, /menu on to bring them back",
        reply_markup=_spoon_keyboard(),
    )


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update):
        return
    arg = (ctx.args[0].lower() if ctx.args else "on")
    if arg in ("off", "hide"):
        await update.message.reply_text("buttons away. /menu on if you want them back.",
                                        reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("there.", reply_markup=_spoon_keyboard())


async def cmd_vibe(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update):
        return
    args = ctx.args or []
    if not args:
        current = db.get_vibe(cfg.database_path, DEFAULT_VIBE)
        lines = ["available vibes:"]
        for p in list_personas():
            marker = " ← current" if p.key == current else ""
            lines.append(f"  /vibe {p.key}  ({p.display}){marker}")
        await update.message.reply_text("\n".join(lines))
        return
    requested = args[0].lower().strip()
    if requested not in CATALOG:
        await update.message.reply_text(
            f"don't know that one. try: {', '.join(CATALOG.keys())}"
        )
        return
    db.set_vibe(cfg.database_path, requested)
    await update.message.reply_text(f"{CATALOG[requested].display.lower()}, then.")


async def cmd_journal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update):
        return
    tz = ZoneInfo(cfg.timezone)
    today = datetime.now(tz).date()
    for offset in range(8):
        d = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        narrative = db.get_journal(cfg.database_path, d)
        if narrative:
            label = _friendly_date_label(today, offset)
            await update.message.reply_text(f"{label}\n\n{narrative}")
            return
    await update.message.reply_text("nothing yet. give it a day.")


async def cmd_weave_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update):
        return
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await _run_scribe_pass(ctx.application)
    await _run_weaver(ctx.application)
    today = _today_local(cfg)
    narrative = db.get_journal(cfg.database_path, today)
    if narrative:
        await update.message.reply_text(f"today, the way I'd write it:\n\n{narrative}")
    else:
        await update.message.reply_text("not enough to write yet. talk to me a bit first.")


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand weekly pattern-noticer. Generates fresh, doesn't show stale."""
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update):
        return
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await _run_weekly_weaver(ctx.application, deliver=False)
    today = _today_local(cfg)
    narrative = db.get_weekly_reflection(cfg.database_path, today)
    if not narrative:
        latest = db.get_latest_weekly_reflection(cfg.database_path)
        if latest:
            week_end, narrative = latest
            await update.message.reply_text(
                f"nothing fresh enough to notice yet. last week's notice, ending {week_end}:\n\n{narrative}"
            )
            return
        await update.message.reply_text(
            "not enough days yet for a week to take shape. give it a few more."
        )
        return
    await update.message.reply_text(f"the week, the way it read:\n\n{narrative}")


async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand body-check poll. Was a daily 2pm cron, now opt-in."""
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update):
        return
    await _run_body_check_poll(ctx.application)


async def cmd_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the Scribe's structured extraction. Voice-neutral, read-only.

    /data            → today's merged extraction
    /data yesterday  → yesterday's
    /data week       → last 7 days, one capsule per day
    """
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update) or not update.message:
        return
    tz = ZoneInfo(cfg.timezone)
    today = datetime.now(tz).date()
    args = (ctx.args or [])
    arg = (args[0].strip().lower() if args else "")

    def _merged_for(date_str: str) -> dict[str, object]:
        rows = db.get_extractions_for_date(cfg.database_path, date_str)
        return mind2.merge_payloads(rows)

    if arg in ("", "today"):
        date_str = today.strftime("%Y-%m-%d")
        merged = _merged_for(date_str)
        body = mind2.format_extraction(merged)
        if not body:
            await update.message.reply_text(f"nothing extracted yet for {date_str}.")
            return
        await update.message.reply_text(f"{date_str}\n\n{body}")
        return

    if arg == "yesterday":
        date_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        merged = _merged_for(date_str)
        body = mind2.format_extraction(merged)
        if not body:
            await update.message.reply_text(f"nothing extracted for {date_str}.")
            return
        await update.message.reply_text(f"{date_str}\n\n{body}")
        return

    if arg == "week":
        capsules: list[str] = []
        for offset in range(6, -1, -1):
            d = today - timedelta(days=offset)
            date_str = d.strftime("%Y-%m-%d")
            merged = _merged_for(date_str)
            body = mind2.format_extraction(merged)
            if not body:
                continue
            capsules.append(f"{date_str}\n{body}")
        if not capsules:
            await update.message.reply_text("nothing extracted in the last 7 days.")
            return
        # Telegram has a 4096-char message cap. If the joined output exceeds
        # the safe limit, send each day in its own message.
        joined = "\n\n".join(capsules)
        if len(joined) <= 3800:
            await update.message.reply_text(joined)
            return
        for cap in capsules:
            await update.message.reply_text(cap)
        return

    await update.message.reply_text(
        "usage: /data | /data yesterday | /data week"
    )


async def cmd_sleep(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Tell the bot what to do about its sleep nudge.

    /sleep              → show current state
    /sleep off          → snooze tonight only
    /sleep off forever  → disable sleep nudges permanently
    /sleep on           → re-enable sleep nudges (clears off-forever and tonight's snooze)
    """
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update):
        return
    args = [a.lower() for a in (ctx.args or [])]
    today = _today_local(cfg)

    off_forever = db.sleep_state_get(cfg.database_path, _SS_OFF_FOREVER) == "1"
    snoozed_today = db.sleep_state_get(cfg.database_path, _SS_SNOOZE_DATE) == today

    if not args:
        if off_forever:
            await update.message.reply_text(
                "sleep nudges are off forever. /sleep on if you want them back."
            )
            return
        if snoozed_today:
            await update.message.reply_text(
                "snoozed for tonight. back tomorrow. /sleep off forever to kill it."
            )
            return
        cutoff = (
            cfg.sleep_weekend_hour
            if time_ctx._is_weekend_evening(datetime.now(ZoneInfo(cfg.timezone)))
            else cfg.sleep_weeknight_hour
        )
        await update.message.reply_text(
            f"sleep nudge is on. tonight's cutoff is {cutoff:02d}:00.\n"
            "/sleep off  → just tonight  ·  /sleep off forever  → kill it"
        )
        return

    if args[0] == "on":
        db.sleep_state_delete(cfg.database_path, _SS_OFF_FOREVER)
        db.sleep_state_delete(cfg.database_path, _SS_SNOOZE_DATE)
        await update.message.reply_text("sleep nudge back on.")
        return

    if args[0] == "off":
        if len(args) > 1 and args[1] == "forever":
            db.sleep_state_set(cfg.database_path, _SS_OFF_FOREVER, "1")
            await update.message.reply_text(
                "off forever. /sleep on if you change your mind."
            )
            return
        db.sleep_state_set(cfg.database_path, _SS_SNOOZE_DATE, today)
        await update.message.reply_text(
            "snoozed for tonight. back tomorrow."
        )
        return

    await update.message.reply_text(
        "didn't catch that. try /sleep, /sleep off, /sleep off forever, /sleep on."
    )


# ----------------------------- input handlers -----------------------------

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    client: AsyncOpenAI = ctx.application.bot_data["openai"]
    if not _is_owner(cfg, update) or not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

    # Input cap: a single Telegram message can technically reach 4096 chars,
    # but a paste-as-message can chain. Truncate quietly with a tail marker so
    # the model sees the bulk and the persona doesn't choke on a token wall.
    if len(text) > MAX_TEXT_CHARS:
        log.info("on_message: trimming oversized input from %d chars", len(text))
        text = text[:MAX_TEXT_CHARS] + "\n\n…[trimmed]"

    local_date = _today_local(cfg)
    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)

    # 1. Low-Spoon button tap?
    state = low_spoon.state_for_label(text)
    if state:
        db.insert_status_event(cfg.database_path, local_date=local_date, state=state)
        ack = low_spoon.acknowledge(vibe_key, state)
        # The button tap itself becomes a user-line in the chat stream so the
        # Scribe sees it as natural context, not a system marker.
        db.insert_message(cfg.database_path, local_date=local_date,
                          role="user", vibe=vibe_key, content=text)
        db.insert_message(cfg.database_path, local_date=local_date,
                          role="assistant", vibe=vibe_key, content=ack)
        await update.message.reply_text(ack)
        return

    # 2. Rabbit hole? (URL or "fun fact:" pattern)
    urls = URL_RE.findall(text)
    looks_like_fact = any(text.lower().startswith(p) for p in
                          ("fun fact", "tdil", "til ", "did you know", "weird:"))
    if urls or looks_like_fact:
        # Try to pull a quick topic phrase before reacting, but never block the
        # chat for more than 3s on it. If the topic call is slow/fails, the
        # persona just reacts without it; the periodic Scribe will backfill.
        first_url = urls[0] if urls else None
        topic: str | None = None
        try:
            topic = await asyncio.wait_for(
                mind2.topic_for(client, model=cfg.mind2_model,
                                raw_text=text, url=first_url),
                timeout=3.0,
            )
        except (asyncio.TimeoutError, Exception):
            topic = None

        # Persist row(s) with the topic if we got one
        for u in (urls or [None]):
            db.insert_rabbit_hole(cfg.database_path, local_date=local_date,
                                  raw_text=text, url=u, topic=topic)

        db.insert_message(cfg.database_path, local_date=local_date,
                          role="user", vibe=vibe_key, content=text)
        await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            reply = await mind1.react_to_rabbit_hole(
                client, model=cfg.mind1_model, vibe_key=vibe_key,
                raw_text=text, topic=topic,
            )
        except Exception:
            log.exception("rabbit-hole persona reaction failed")
            reply = "yeah, that's a hole worth falling into."
        db.insert_message(cfg.database_path, local_date=local_date,
                          role="assistant", vibe=vibe_key, content=reply)
        await update.message.reply_text(reply)
        return

    # 3. Normal chat — Mind 1 handles it
    # Build BOTH context notes BEFORE we insert the new user message, so the
    # message-context query reads only the prior history (otherwise gap_user
    # would always be ~0).
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

    db.insert_message(cfg.database_path, local_date=local_date,
                      role="user", vibe=vibe_key, content=text)
    rows = db.get_recent_messages(cfg.database_path, limit=20)
    history = [{"role": r["role"], "content": r["content"]} for r in rows[:-1]]
    voice_samples = db.get_user_voice_samples(cfg.database_path, limit=8)

    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
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

    log.info(
        "on_message: state=%s burst=%s tz_band=%s",
        mc.session_state, mc.is_burst, tc.time_band,
    )

    db.insert_message(cfg.database_path, local_date=local_date,
                      role="assistant", vibe=vibe_key, content=reply)
    await update.message.reply_text(reply)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Photo handler: silent acknowledgment only. We don't run vision, store
    photos, or feed them to the Scribe/Weaver. Just a short in-vibe reply so
    the chat doesn't go dead when the user sends an image.
    """
    cfg: Config = ctx.application.bot_data["cfg"]
    if not _is_owner(cfg, update) or not update.message or not update.message.photo:
        return
    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)
    reply = nudges.ack_photo(vibe_key)
    log.info("photo received, ack-only (vibe=%s)", vibe_key)
    await update.message.reply_text(reply)


async def on_poll_answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    pa: PollAnswer | None = update.poll_answer
    if not pa or pa.user.id != cfg.telegram_owner_id:
        return
    db.record_poll_answer(cfg.database_path, pa.poll_id, list(pa.option_ids))
    log.info("poll answer recorded: %s", pa.option_ids)


# ----------------------------- jobs -----------------------------

async def _run_scribe_pass(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    client: AsyncOpenAI = app.bot_data["openai"]
    local_date = _today_local(cfg)

    # Part A: chat exhaust → JSON extraction
    rows = db.get_unscribed_for_date(cfg.database_path, local_date)
    if rows:
        transcript_lines = [f"[{r['role']}] {r['content']}" for r in rows]
        log.info("scribe: extracting from %d new lines on %s", len(rows), local_date)
        try:
            payload = await mind2.extract(client, model=cfg.mind2_model,
                                           transcript_lines=transcript_lines)
            db.insert_extraction(cfg.database_path, local_date, payload)
            db.mark_scribed(cfg.database_path, [int(r["id"]) for r in rows])
        except Exception:
            log.exception("Mind 2 failure")
    else:
        log.info("scribe: nothing new for %s", local_date)

    # Part B: backfill rabbit-hole topics (cheap per-item call, capped at 20/pass)
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


async def _run_weaver(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    client: AsyncOpenAI = app.bot_data["openai"]
    local_date = _today_local(cfg)

    chat_signals = mind2.merge_payloads(db.get_extractions_for_date(cfg.database_path, local_date))
    status_taps = db.get_status_events_for_date(cfg.database_path, local_date)
    polls = db.get_polls_for_date(cfg.database_path, local_date)
    rabbits = db.get_rabbit_holes_for_date(cfg.database_path, local_date)

    has_any_signal = (
        any(chat_signals.get("gratitudes")) or
        chat_signals.get("mental_weather") or
        any(v.get("status", "unknown") != "unknown"
            for v in chat_signals.get("meat_suit_maintenance", {}).values()) or
        status_taps or polls or rabbits
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

    # Day-shape hint: pull all of today's user messages and let time_ctx
    # produce a 1-line rhythm note.
    timing_rows = db.get_message_timing_window(cfg.database_path, limit=200)
    user_ts_today = [
        row["ts_utc"] for row in timing_rows if row["role"] == "user"
    ]
    rhythm_note = time_ctx.rhythm_summary(user_ts_today, timezone=cfg.timezone) or None

    try:
        narrative = await mind3.weave(
            client, model=cfg.mind3_model, merged_payload=bundle, local_date=local_date,
            voice_samples=voice_samples,
            rhythm_note=rhythm_note,
        )
        db.upsert_journal(cfg.database_path, local_date, narrative, bundle)
        # Deliver as if you wrote it tonight. No 'ghostwritten' header, no date
        # stamp — those land like system receipts. Just a soft handoff line.
        await app.bot.send_message(
            chat_id=cfg.telegram_owner_id,
            text=f"how today read, in your voice:\n\n{narrative}",
        )
    except Exception:
        log.exception("Mind 3 failure")


async def _run_weekly_weaver(app: Application, *, deliver: bool) -> None:
    """Sunday-night pattern-noticer. Reads up to 7 daily journals + signals,
    writes one short paragraph in the user's voice. If `deliver` is True,
    sends it to the user. Otherwise just stashes it for /week to fetch.
    """
    cfg: Config = app.bot_data["cfg"]
    client: AsyncOpenAI = app.bot_data["openai"]
    today = _today_local(cfg)

    daily = db.get_recent_journals(cfg.database_path, end_date=today, days=7)
    if len(daily) < 2:
        log.info("weekly: only %d daily entries, skipping", len(daily))
        return

    voice_samples = db.get_user_voice_samples(cfg.database_path, limit=16)
    # Late-night dates from the same daily entries the Weaver is reading.
    # Each daily journal's source_json carries message timestamps via the
    # merged signals; if the field isn't there, late_night_days returns [].
    late_dates = time_ctx.late_night_days(daily, timezone=cfg.timezone)
    try:
        narrative = await mind3.weave_week(
            client, model=cfg.mind3_model,
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
    if deliver:
        try:
            await app.bot.send_message(
                chat_id=cfg.telegram_owner_id,
                text=f"the week, the way it read:\n\n{narrative}",
            )
        except Exception:
            log.exception("weekly weaver delivery failed")


async def _run_nudge(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    if not cfg.nudges_enabled:
        return
    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)
    hour = datetime.now(ZoneInfo(cfg.timezone)).hour
    text = nudges.pick(vibe_key, hour=hour)
    db.insert_message(cfg.database_path, local_date=_today_local(cfg),
                      role="assistant", vibe=vibe_key, content=text)
    try:
        await app.bot.send_message(chat_id=cfg.telegram_owner_id, text=text)
    except Exception:
        log.exception("nudge send failure")


async def _run_sleep_nudge(app: Application) -> None:
    """Nightly cutoff check. If the user is past their cutoff and hasn't opted
    out, send one soft observation — NOT a command.

    Skips when:
    - sleep_nudges_enabled is False at config level
    - off_forever flag is set
    - snooze_date == today
    - already nudged today (idempotent: don't double-fire on a restart)
    - we're not actually past the cutoff yet (sanity)
    """
    cfg: Config = app.bot_data["cfg"]
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
        # Cron fired but we're not actually past cutoff. Common at the
        # boundary minute. Bail.
        return

    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)
    msg = nudges.pick_sleep(vibe_key)

    db.insert_message(cfg.database_path, local_date=today,
                      role="system_nudge", vibe=vibe_key, content=msg)
    db.sleep_state_set(cfg.database_path, _SS_LAST_NUDGE_DATE, today)
    try:
        await app.bot.send_message(chat_id=cfg.telegram_owner_id, text=msg)
    except Exception:
        log.exception("sleep nudge send failure")


async def _run_body_check_poll(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    try:
        msg = await app.bot.send_poll(
            chat_id=cfg.telegram_owner_id,
            question=body_check.POLL_QUESTION,
            options=body_check.POLL_OPTIONS,
            is_anonymous=False,           # required to receive poll_answer updates
            allows_multiple_answers=True,
        )
        db.insert_body_check_poll(
            cfg.database_path,
            local_date=_today_local(cfg),
            poll_id=msg.poll.id,
            options=list(body_check.POLL_OPTIONS),
        )
    except Exception:
        log.exception("body-check poll send failed")


# Job-queue wrappers
async def _job_scribe(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_scribe_pass(ctx.application)

async def _job_weaver(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_scribe_pass(ctx.application)
    await _run_weaver(ctx.application)

async def _job_nudge(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_nudge(ctx.application)

async def _job_weekly_weaver(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_weekly_weaver(ctx.application, deliver=True)

async def _job_sleep_nudge(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_sleep_nudge(ctx.application)


# ----------------------------- bootstrap -----------------------------

def build_app(cfg: Config) -> Application:
    db.init_db(cfg.database_path)

    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data["cfg"] = cfg
    # The OpenAI Python SDK is used as the client, but the base_url and key can
    # point at any OpenAI-compatible provider. Default is Gemini's free-tier
    # endpoint; flip LLM_BASE_URL + LLM_API_KEY for OpenAI, Groq, OpenRouter, etc.
    app.bot_data["openai"] = AsyncOpenAI(
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("vibe", cmd_vibe))
    app.add_handler(CommandHandler("journal", cmd_journal))
    app.add_handler(CommandHandler("weave_now", cmd_weave_now))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("data", cmd_data))
    app.add_handler(CommandHandler("sleep", cmd_sleep))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(PollAnswerHandler(on_poll_answer))

    tz = ZoneInfo(cfg.timezone)
    jq = app.job_queue

    jq.run_repeating(_job_scribe, interval=timedelta(minutes=30), first=timedelta(minutes=5))
    jq.run_daily(_job_weaver, time=dtime(hour=23, minute=59, tzinfo=tz))
    # Sunday night pattern-noticer. python-telegram-bot v20+ uses
    # 0=Sunday..6=Saturday for run_daily's `days` parameter, so Sunday=0.
    jq.run_daily(
        _job_weekly_weaver,
        time=dtime(hour=22, minute=0, tzinfo=tz),
        days=(0,),
    )

    if cfg.nudges_enabled:
        # Text nudges: 11:00 + 17:30. No daily body-check poll — it's now /check on demand,
        # because a recurring questionnaire is the kind of intrusion this whole project
        # is supposed to avoid.
        for h, m in [(11, 0), (17, 30)]:
            jq.run_daily(_job_nudge, time=dtime(hour=h, minute=m, tzinfo=tz))

    if cfg.sleep_nudges_enabled:
        # Sleep nudge: fires at the configured cutoff hour each night. The job
        # itself decides whether to actually send (snooze flag, off-forever,
        # already nudged today). One job per cutoff so weeknight + weekend
        # both fire — the job re-checks which one is active before sending.
        for hour in {cfg.sleep_weeknight_hour, cfg.sleep_weekend_hour}:
            jq.run_daily(_job_sleep_nudge, time=dtime(hour=hour, minute=0, tzinfo=tz))

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    app = build_app(cfg)
    log.info("daycatcher v2 starting, owner=%s, tz=%s", cfg.telegram_owner_id, cfg.timezone)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
