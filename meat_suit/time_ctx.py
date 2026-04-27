"""Time context. Tells the persona what time it is without making it a clock.

The persona doesn't have a concept of time on its own. This module computes a
TimeContext from `now()` and the user's configured cutoffs, formats it into a
small system-note for Mind 1, and computes a rhythm summary from a list of
message timestamps for the Weaver.

Pure functions. No I/O. No DB. No telegram. Tested by example.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo


# ----------------------------- model -----------------------------

@dataclass(frozen=True)
class TimeContext:
    local_dt: datetime
    day_name: str            # "Monday"
    is_weekend: bool         # Friday/Saturday count as "tomorrow is not a workday"
    time_band: str           # 'late_night' | 'small_hours' | 'morning' | 'midday' |
                             # 'afternoon' | 'evening' | 'wind_down'
    cutoff_hour: int         # the active sleep cutoff in local hours (0-23)
    sleep_window_active: bool   # are we past cutoff?
    hours_past_cutoff: float    # 0.0 if not past; how many hours past otherwise
    minutes_to_cutoff: int      # minutes until cutoff if not past, else 0


# Bound on hours-past so a clock skew or stale clock doesn't make the persona
# think the user has been awake for 30 hours. After this much time, we assume
# the user slept and call the night over.
_MAX_OVERSHOOT_HOURS = 9.0

# After this many hours past midnight, we're in "new day" territory; stop
# blaming last night's cutoff. e.g. 6am Thursday isn't "still up from Wednesday."
_NEW_DAY_HOUR = 7


# ----------------------------- knobs -----------------------------

# A "late evening" Friday is a weekend night. A late Sunday is not.
# Treating Fri/Sat as weekend matches how people actually live.
_WEEKEND_DOWS = {4, 5}  # Mon=0..Sun=6

# Used to label time-of-day inside the persona's system note.
def _time_band(local_hour: int) -> str:
    if 0 <= local_hour < 4:
        return "small_hours"
    if 4 <= local_hour < 7:
        return "late_night"   # 4-6am: liminal. Person is either still up or up too early.
    if 7 <= local_hour < 11:
        return "morning"
    if 11 <= local_hour < 14:
        return "midday"
    if 14 <= local_hour < 17:
        return "afternoon"
    if 17 <= local_hour < 21:
        return "evening"
    return "wind_down"        # 21-23


def _is_weekend_evening(local_dt: datetime) -> bool:
    """A Friday at 11pm is 'weekend' for sleep purposes. A Sunday at 11pm is not.

    Saturday is fully weekend. Friday counts after 6pm. Sunday never counts.
    """
    dow = local_dt.weekday()
    if dow == 5:  # Saturday
        return True
    if dow == 4 and local_dt.hour >= 18:  # Friday evening into Saturday morning
        return True
    if dow == 6 and local_dt.hour < 4:    # Saturday night spilled into Sunday small hours
        return True
    return False


# ----------------------------- builders -----------------------------

def build_context(
    *,
    now_local: datetime,
    weeknight_cutoff_hour: int,
    weekend_cutoff_hour: int,
) -> TimeContext:
    """Compute the time context. Pass `now_local` (already TZ-aware in the
    user's local tz). Returns a fully populated TimeContext.

    Semantics:
    - If we're in the morning (before _NEW_DAY_HOUR), the "active cutoff" is
      *yesterday* evening's cutoff. We may be past it.
    - If we're after _NEW_DAY_HOUR, last night is over. The active cutoff is
      tonight's, which is in the future.
    - The cutoff hour is selected based on which calendar night we are
      currently inside (the night that *just happened* if it's morning, or
      the night ahead of us if it's evening).
    """
    if now_local.tzinfo is None:
        raise ValueError("now_local must be timezone-aware")
    if not (0 <= weeknight_cutoff_hour <= 23):
        raise ValueError(f"weeknight_cutoff_hour out of range: {weeknight_cutoff_hour}")
    if not (0 <= weekend_cutoff_hour <= 23):
        raise ValueError(f"weekend_cutoff_hour out of range: {weekend_cutoff_hour}")

    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    # Step 1: figure out which "night" we're inside. If now is in early morning
    # (before _NEW_DAY_HOUR), we're still inside last night's sleep window.
    # Otherwise we're past it; the relevant night is the upcoming one.
    inside_prev_night = now_local.hour < _NEW_DAY_HOUR

    if inside_prev_night:
        # Last night's cutoff governs. The cutoff fired either yesterday
        # evening (e.g. weeknight 23:00) or earlier this morning (e.g.
        # weekend 01:00). Anchor the weekend check at yesterday 22:00 so we
        # land squarely inside the prior evening regardless of how early in
        # the morning we are right now.
        yesterday = today - timedelta(days=1)
        anchor = yesterday.replace(hour=22)
        is_weekend = _is_weekend_evening(anchor)
        cutoff_hour = weekend_cutoff_hour if is_weekend else weeknight_cutoff_hour
        if cutoff_hour < _NEW_DAY_HOUR:
            # Cutoff like 01:00 on a weekend night => fired earlier today.
            cutoff_dt = today.replace(hour=cutoff_hour)
        else:
            # Cutoff like 23:00 weeknight => fired yesterday evening.
            cutoff_dt = (today - timedelta(days=1)).replace(hour=cutoff_hour)
    else:
        # We're in the day. Tonight's cutoff is upcoming.
        is_weekend = _is_weekend_evening(now_local)
        cutoff_hour = weekend_cutoff_hour if is_weekend else weeknight_cutoff_hour
        if cutoff_hour < _NEW_DAY_HOUR:
            # Cutoff like 01:00 means tomorrow morning's hour.
            cutoff_dt = (today + timedelta(days=1)).replace(hour=cutoff_hour)
        else:
            cutoff_dt = today.replace(hour=cutoff_hour)

    delta = now_local - cutoff_dt
    seconds = delta.total_seconds()
    if seconds >= 0:
        hours_past = seconds / 3600.0
        # Sanity bound: if it's been more than _MAX_OVERSHOOT_HOURS, treat as
        # "the night is over." Caller is past the cutoff but we don't want a
        # 30-hour escalation reading.
        if hours_past > _MAX_OVERSHOOT_HOURS:
            sleep_window_active = False
            hours_past_cutoff = 0.0
            minutes_to_cutoff = 0  # not meaningful here; next cutoff is tonight
        else:
            sleep_window_active = True
            hours_past_cutoff = hours_past
            minutes_to_cutoff = 0
    else:
        sleep_window_active = False
        hours_past_cutoff = 0.0
        minutes_to_cutoff = max(0, int(-seconds // 60))

    return TimeContext(
        local_dt=now_local,
        day_name=now_local.strftime("%A"),
        is_weekend=is_weekend,
        time_band=_time_band(now_local.hour),
        cutoff_hour=cutoff_hour,
        sleep_window_active=sleep_window_active,
        hours_past_cutoff=hours_past_cutoff,
        minutes_to_cutoff=minutes_to_cutoff,
    )


def now_context(
    *,
    timezone: str,
    weeknight_cutoff_hour: int,
    weekend_cutoff_hour: int,
) -> TimeContext:
    """Convenience wrapper that calls datetime.now() in the user's tz."""
    now_local = datetime.now(ZoneInfo(timezone))
    return build_context(
        now_local=now_local,
        weeknight_cutoff_hour=weeknight_cutoff_hour,
        weekend_cutoff_hour=weekend_cutoff_hour,
    )


# ----------------------------- persona note -----------------------------

def _portable_clock(dt: datetime) -> str:
    """12-hour clock string without leading zero. POSIX `%-I` is not portable;
    Windows uses `%#I`. Build it manually so we don't fork on platform.
    """
    hour12 = dt.hour % 12 or 12
    return f"{hour12}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def format_for_persona(tc: TimeContext) -> str:
    """A short system-note appended to the persona's prompt.

    Designed so the persona doesn't feel obliged to mention time. It's
    available context, not a topic. Most replies will not reference it.
    """
    clock = _portable_clock(tc.local_dt)
    band_label = {
        "small_hours": "small hours of the morning",
        "late_night": "very early morning, pre-dawn",
        "morning": "morning",
        "midday": "midday",
        "afternoon": "afternoon",
        "evening": "evening",
        "wind_down": "late evening, normal wind-down time",
    }[tc.time_band]

    parts = [
        f"Right now it is {tc.day_name}, {clock} ({band_label}).",
    ]
    if tc.sleep_window_active:
        if tc.hours_past_cutoff < 1:
            parts.append("The user is just past their wind-down cutoff for tonight.")
        elif tc.hours_past_cutoff < 2:
            parts.append(
                "The user is over an hour past their wind-down cutoff. "
                "If you say anything about sleep, say it once and stay soft."
            )
        else:
            parts.append(
                "The user is well past their cutoff and clearly still up. "
                "Do not generate proactive content or new threads. "
                "Reply briefly to whatever they say. Do not lecture."
            )
    parts.append(
        "Use this only when it's actually relevant to what they said. "
        "Do not announce the time. Do not mention this note. "
        "If their message has nothing to do with time, neither does your reply."
    )
    return " ".join(parts)


# ----------------------------- rhythm (for Weaver) -----------------------------

def rhythm_summary(
    timestamps_utc_iso: Iterable[str],
    *,
    timezone: str,
) -> str:
    """Given the day's user-message timestamps (UTC ISO strings), describe the
    shape of the day. Empty string if nothing notable.

    Keep this concise: the Weaver consumes it as a 1-line hint.
    """
    tz = ZoneInfo(timezone)
    locals_: list[datetime] = []
    for ts in timestamps_utc_iso:
        try:
            # SQLite TEXT stored as 'YYYY-MM-DDTHH:MM:SS+00:00' or similar
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            locals_.append(dt.astimezone(tz))
        except (ValueError, AttributeError):
            continue

    n = len(locals_)
    if n == 0:
        return ""

    hours = [d.hour for d in locals_]
    morning = sum(1 for h in hours if 5 <= h < 12)
    afternoon = sum(1 for h in hours if 12 <= h < 17)
    evening = sum(1 for h in hours if 17 <= h < 23)
    late = sum(1 for h in hours if h >= 23 or h < 5)

    notes: list[str] = []

    # Late-night flag
    if late >= 3:
        # If any messages happened past midnight, surface the latest one.
        post_mid = [d for d in locals_ if d.hour < 5]
        if post_mid:
            latest_post = max(post_mid, key=lambda d: (d.hour, d.minute))
            notes.append(
                f"chat ran past midnight; latest message at "
                f"{_portable_clock(latest_post)}"
            )
        else:
            notes.append("late-night chatter, after 11pm")

    # Day shape
    if n >= 4:
        if evening + late >= int(0.7 * n):
            notes.append("the day was mostly after 5pm")
        elif morning >= int(0.7 * n):
            notes.append("the day front-loaded into the morning")
        elif afternoon + evening >= int(0.7 * n) and morning <= 1:
            notes.append("the morning was thin")

    # Fragmentation: if messages span >12 hours but with a ≥4-hour gap somewhere
    if n >= 3:
        sorted_locals = sorted(locals_)
        max_gap = max(
            (b - a).total_seconds() / 3600.0
            for a, b in zip(sorted_locals, sorted_locals[1:])
        )
        span = (sorted_locals[-1] - sorted_locals[0]).total_seconds() / 3600.0
        if span >= 8 and max_gap >= 4:
            notes.append(f"a {int(max_gap)}-hour quiet gap in the middle")

    return "; ".join(notes)


# ----------------------------- between-message context -----------------------------

# Thresholds that decide what "the moment" is.
_BURST_WINDOW_SECONDS = 120          # 2 minutes
_BURST_MIN_USER_MESSAGES = 4         # 4+ user messages in 2min => burst
_MID_THREAD_MAX_GAP_MIN = 30         # gap < 30min is the same conversation
_PAUSED_MAX_GAP_HOURS = 4            # 30min - 4h is a paused-but-same session
_SAME_DAY_MAX_GAP_HOURS = 12         # 4h - 12h is new session, same day
_RETURNING_MAX_GAP_HOURS = 72        # 12h - 72h is a return after a quiet stretch
# > 72h is "long absence"


@dataclass(frozen=True)
class MessageContext:
    """What the persona should know about *between-message* state.

    Computed from the existing message stream. No new persistence needed.
    """
    # gap_user_seconds: time since the user's previous message before this one.
    # None when this is their first message ever.
    gap_user_seconds: float | None
    # gap_assistant_seconds: time since the bot's last outgoing message (any
    # role 'assistant' or 'system_nudge'). None if the bot has never spoken.
    gap_assistant_seconds: float | None
    # bot_last_was_nudge: was the bot's most recent outgoing message an
    # unprompted nudge? If so, the user is replying to it and the gap
    # shouldn't be read as awkward silence.
    bot_last_was_nudge: bool
    # session_state: classification of the current moment.
    # 'first_ever' | 'mid_thread' | 'paused' | 'new_session_same_day'
    # | 'returning' | 'long_absence'
    session_state: str
    # is_burst: 4+ user messages in the last 2 minutes (counting THIS one).
    is_burst: bool
    # user_messages_in_burst_window: count for context (only meaningful when
    # is_burst is true, but always populated).
    user_messages_in_burst_window: int


@dataclass(frozen=True)
class MessageRecord:
    """Lightweight record for build_message_context. Pass the most recent
    history (oldest-first or newest-first; we sort).

    `role` should be one of 'user', 'assistant', 'system_nudge'.
    """
    ts_utc_iso: str
    role: str


def _classify_session_state(
    gap_user_seconds: float | None,
) -> str:
    if gap_user_seconds is None:
        return "first_ever"
    minutes = gap_user_seconds / 60.0
    hours = gap_user_seconds / 3600.0
    if minutes < _MID_THREAD_MAX_GAP_MIN:
        return "mid_thread"
    if hours < _PAUSED_MAX_GAP_HOURS:
        return "paused"
    if hours < _SAME_DAY_MAX_GAP_HOURS:
        return "new_session_same_day"
    if hours < _RETURNING_MAX_GAP_HOURS:
        return "returning"
    return "long_absence"


def build_message_context(
    *,
    now_utc: datetime,
    history: Sequence[MessageRecord],
) -> MessageContext:
    """Build a MessageContext from `now_utc` and a recent message history.

    `history` should NOT include the message currently being processed.
    It's the prior context. Pass at least the last ~50 messages so the
    burst window has enough room to be honest.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")

    # Parse + sort newest-first.
    parsed: list[tuple[datetime, str]] = []
    for rec in history:
        try:
            dt = datetime.fromisoformat(str(rec.ts_utc_iso).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                # Treat naive timestamps as UTC. SQLite layer should always
                # write tz-aware ISO, but be defensive.
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        except (ValueError, AttributeError):
            continue
        parsed.append((dt, rec.role))
    parsed.sort(key=lambda t: t[0], reverse=True)

    # Find most recent USER and most recent BOT (assistant or nudge).
    last_user_dt: datetime | None = None
    last_bot_dt: datetime | None = None
    last_bot_role: str | None = None
    for dt, role in parsed:
        if last_user_dt is None and role == "user":
            last_user_dt = dt
        if last_bot_dt is None and role in ("assistant", "system_nudge"):
            last_bot_dt = dt
            last_bot_role = role
        if last_user_dt is not None and last_bot_dt is not None:
            break

    gap_user_seconds = (
        (now_utc - last_user_dt).total_seconds() if last_user_dt else None
    )
    gap_assistant_seconds = (
        (now_utc - last_bot_dt).total_seconds() if last_bot_dt else None
    )
    bot_last_was_nudge = last_bot_role == "system_nudge"

    # Burst: count user messages within the last _BURST_WINDOW_SECONDS,
    # *plus* the current message being processed (the +1 below).
    window_start = now_utc - timedelta(seconds=_BURST_WINDOW_SECONDS)
    user_in_window = sum(
        1 for dt, role in parsed
        if role == "user" and dt >= window_start
    )
    user_messages_in_burst_window = user_in_window + 1  # include current
    is_burst = user_messages_in_burst_window >= _BURST_MIN_USER_MESSAGES

    return MessageContext(
        gap_user_seconds=gap_user_seconds,
        gap_assistant_seconds=gap_assistant_seconds,
        bot_last_was_nudge=bot_last_was_nudge,
        session_state=_classify_session_state(gap_user_seconds),
        is_burst=is_burst,
        user_messages_in_burst_window=user_messages_in_burst_window,
    )


def _humanize_seconds(seconds: float) -> str:
    """'3 minutes' / '4 hours' / '2 days'. Rounded down. No 'about'."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute" + ("s" if minutes != 1 else "")
    hours = int(seconds // 3600)
    if hours < 36:
        return f"{hours} hour" + ("s" if hours != 1 else "")
    days = int(seconds // 86400)
    return f"{days} day" + ("s" if days != 1 else "")


def format_message_context_for_persona(mc: MessageContext) -> str:
    """Render the MessageContext into a system-note for the persona.

    Trimmed scope: we only emit a note for the two states where the model
    reliably fails on its own. Everything else returns empty.

    - long_absence: model otherwise greets / asks where they were.
    - bot_last_was_nudge: model otherwise scolds the user for being slow.

    Design rule: tell the persona what the moment IS and what is FORBIDDEN.
    Do not tell it to "acknowledge" anything. Models comply with explicit
    instructions to acknowledge by performing a greeting; that's exactly
    the failure mode we are trying to prevent.

    Other session states (first_ever, mid_thread, paused, new_session_same_day,
    returning) return empty. The model handles those well from message-history
    timestamps alone, and adding instructions just bloats the prompt.
    """
    parts: list[str] = []

    if mc.session_state == "long_absence":
        gap = _humanize_seconds(mc.gap_user_seconds or 0)
        parts.append(
            f"The user has been away for {gap}. "
            "Do NOT say 'welcome back.' Do NOT ask where they were. "
            "Do NOT remark on the silence at all. "
            "The silence is not a problem they need to explain. "
            "Just respond to what they actually said."
        )

    if mc.bot_last_was_nudge and mc.gap_assistant_seconds is not None:
        gap_a = _humanize_seconds(mc.gap_assistant_seconds)
        parts.append(
            f"Your last message was an unprompted nudge sent {gap_a} ago. "
            "They are replying to it now. They are not late. "
            "Do not acknowledge timing. Just respond to the content."
        )

    return " ".join(parts)


# ----------------------------- weekly: late-night days -----------------------------

def late_night_days(
    daily_entries: list[dict],
    *,
    timezone: str,
) -> list[str]:
    """For the weekly Weaver. Given list of {local_date, signals: {...}}, return
    the dates where chat ran past midnight.

    Each entry may contain 'message_timestamps_utc' (list of ISO strings) inside
    its signals. Missing field => skipped. Returns YYYY-MM-DD strings.
    """
    out: list[str] = []
    tz = ZoneInfo(timezone)
    for entry in daily_entries:
        signals = entry.get("signals") or {}
        ts_list = signals.get("message_timestamps_utc") or []
        for ts in ts_list:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(tz)
            except (ValueError, AttributeError):
                continue
            # "past midnight" = hour 0..4 on the date AFTER the entry's local_date
            # OR hour >= 23 same day, but we count both as the date the day "owns"
            if dt.hour >= 23 or dt.hour < 4:
                out.append(entry.get("local_date", ""))
                break
    return [d for d in out if d]
