"""Telegram bot — front door for the Persona, plus visual/haptic intake & cron."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time as dtime, timedelta
from io import BytesIO
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

from . import body_check, db, low_spoon, nudges
from .config import Config, load_config
from .minds import eye as mind4
from .minds import persona as mind1
from .minds import scribe as mind2
from .minds import weaver as mind3
from .personas.catalog import CATALOG, DEFAULT_VIBE, list_personas

log = logging.getLogger("meatsuit")

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


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


# ----------------------------- input handlers -----------------------------

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    client: AsyncOpenAI = ctx.application.bot_data["openai"]
    if not _is_owner(cfg, update) or not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    if not text:
        return

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
        )
    except Exception:
        log.exception("Mind 1 failure")
        reply = "lost the thread for a sec. say it again?"

    db.insert_message(cfg.database_path, local_date=local_date,
                      role="assistant", vibe=vibe_key, content=reply)
    await update.message.reply_text(reply)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = ctx.application.bot_data["cfg"]
    client: AsyncOpenAI = ctx.application.bot_data["openai"]
    if not _is_owner(cfg, update) or not update.message or not update.message.photo:
        return

    local_date = _today_local(cfg)
    vibe_key = db.get_vibe(cfg.database_path, DEFAULT_VIBE)

    # Best-quality photo
    photo = update.message.photo[-1]
    caption = (update.message.caption or "").strip() or None

    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        f = await ctx.bot.get_file(photo.file_id)
        buf = BytesIO()
        await f.download_to_memory(buf)
        image_bytes = buf.getvalue()
    except Exception:
        log.exception("photo download failed")
        await update.message.reply_text("the image didn't come through. send it again?")
        return

    # Mind 4: neutral observation + structured signals
    try:
        eye = await mind4.see(client, model=cfg.mind3_model, image_bytes=image_bytes, caption=caption)
    except Exception:
        log.exception("Mind 4 failure")
        eye = {"observation": "(image received, vision unavailable)", "signals": {}}

    db.insert_photo_observation(
        cfg.database_path,
        local_date=local_date,
        file_id=photo.file_id,
        caption=caption,
        observation=eye["observation"],
        signals=eye.get("signals", {}),
    )

    # Mind 1: react in-character
    try:
        reply = await mind1.react_to_image(
            client, model=cfg.mind1_model, vibe_key=vibe_key,
            neutral_observation=eye["observation"], user_caption=caption,
        )
    except Exception:
        log.exception("persona image reaction failed")
        reply = "yeah."

    # Stream into messages so the Scribe gets context too. We use the user's
    # caption verbatim if they wrote one; otherwise a soft note that they sent
    # a photo (no [brackets], no [tags], so the chat reads like a chat).
    user_log = caption if caption else "(sent a photo)"
    db.insert_message(cfg.database_path, local_date=local_date,
                      role="user", vibe=vibe_key, content=user_log)
    db.insert_message(cfg.database_path, local_date=local_date,
                      role="assistant", vibe=vibe_key, content=reply)
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
    photos = db.get_photo_observations_for_date(cfg.database_path, local_date)
    rabbits = db.get_rabbit_holes_for_date(cfg.database_path, local_date)

    has_any_signal = (
        any(chat_signals.get("gratitudes")) or
        chat_signals.get("mental_weather") or
        any(v.get("status", "unknown") != "unknown"
            for v in chat_signals.get("meat_suit_maintenance", {}).values()) or
        status_taps or polls or photos or rabbits
    )
    if not has_any_signal:
        log.info("weaver: no signals for %s, skipping", local_date)
        return

    bundle = {
        "chat_signals": chat_signals,
        "status_taps": status_taps,
        "body_check_poll": polls[-1] if polls else None,
        "photo_observations": photos,
        "rabbit_holes": rabbits,
    }
    voice_samples = db.get_user_voice_samples(cfg.database_path, limit=12)
    try:
        narrative = await mind3.weave(
            client, model=cfg.mind3_model, merged_payload=bundle, local_date=local_date,
            voice_samples=voice_samples,
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
    try:
        narrative = await mind3.weave_week(
            client, model=cfg.mind3_model,
            week_end_date=today,
            daily_entries=daily,
            voice_samples=voice_samples,
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
    text = nudges.pick(vibe_key)
    db.insert_message(cfg.database_path, local_date=_today_local(cfg),
                      role="assistant", vibe=vibe_key, content=text)
    try:
        await app.bot.send_message(chat_id=cfg.telegram_owner_id, text=text)
    except Exception:
        log.exception("nudge send failure")


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


# ----------------------------- bootstrap -----------------------------

def build_app(cfg: Config) -> Application:
    db.init_db(cfg.database_path)

    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["openai"] = AsyncOpenAI(api_key=cfg.openai_api_key)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("vibe", cmd_vibe))
    app.add_handler(CommandHandler("journal", cmd_journal))
    app.add_handler(CommandHandler("weave_now", cmd_weave_now))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("check", cmd_check))
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

    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = load_config()
    app = build_app(cfg)
    log.info("meat-suit v2 starting, owner=%s, tz=%s", cfg.telegram_owner_id, cfg.timezone)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
