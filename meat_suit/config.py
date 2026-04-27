"""Centralized config. Reads from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required env var: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return val


def _int_in_range(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer (got {raw!r}).")
    if not (lo <= v <= hi):
        raise RuntimeError(f"{name}={v} out of range [{lo},{hi}].")
    return v


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_owner_id: int
    openai_api_key: str
    mind1_model: str
    mind2_model: str
    mind3_model: str
    database_path: Path
    timezone: str
    nudges_enabled: bool
    sleep_weeknight_hour: int      # 0-23, hour of day after which we'd nudge sleep
    sleep_weekend_hour: int        # 0-23, same but for Fri/Sat nights (commonly later)
    sleep_nudges_enabled: bool


def load_config() -> Config:
    db_path = Path(os.getenv("DATABASE_PATH", "./data/meatsuit.db")).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    timezone = os.getenv("TIMEZONE", "America/Chicago")
    # Validate at load time so a bad TZ string doesn't leak into job scheduling.
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"TIMEZONE={timezone!r} is not a known IANA zone. "
            f"Use e.g. 'America/Chicago', 'Europe/London', 'Asia/Tokyo'."
        ) from exc

    return Config(
        telegram_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_owner_id=int(_required("TELEGRAM_OWNER_ID")),
        openai_api_key=_required("OPENAI_API_KEY"),
        mind1_model=os.getenv("MIND1_PERSONA_MODEL", "gpt-4o-mini"),
        mind2_model=os.getenv("MIND2_SCRIBE_MODEL", "gpt-4o-mini"),
        mind3_model=os.getenv("MIND3_WEAVER_MODEL", "gpt-4o"),
        database_path=db_path,
        timezone=timezone,
        nudges_enabled=os.getenv("NUDGES_ENABLED", "1") == "1",
        sleep_weeknight_hour=_int_in_range("SLEEP_WEEKNIGHT_HOUR", 23, 0, 23),
        sleep_weekend_hour=_int_in_range("SLEEP_WEEKEND_HOUR", 1, 0, 23),
        sleep_nudges_enabled=os.getenv("SLEEP_NUDGES_ENABLED", "1") == "1",
    )
