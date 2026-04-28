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
    llm_api_key: str
    llm_base_url: str
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

    # LLM_API_KEY is the canonical name. OPENAI_API_KEY is accepted as fallback
    # so existing .env files keep working without an immediate rewrite.
    llm_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not llm_key:
        raise RuntimeError(
            "Missing required env var: LLM_API_KEY. "
            "Get one free from https://aistudio.google.com/apikey , "
            "or use any OpenAI-compatible provider with LLM_BASE_URL."
        )

    return Config(
        telegram_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_owner_id=int(_required("TELEGRAM_OWNER_ID")),
        llm_api_key=llm_key,
        llm_base_url=os.getenv(
            "LLM_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        mind1_model=os.getenv("MIND1_PERSONA_MODEL", "gemini-2.0-flash"),
        mind2_model=os.getenv("MIND2_SCRIBE_MODEL", "gemini-2.0-flash"),
        mind3_model=os.getenv("MIND3_WEAVER_MODEL", "gemini-2.0-flash"),
        database_path=db_path,
        timezone=timezone,
        nudges_enabled=os.getenv("NUDGES_ENABLED", "1") == "1",
        sleep_weeknight_hour=_int_in_range("SLEEP_WEEKNIGHT_HOUR", 23, 0, 23),
        sleep_weekend_hour=_int_in_range("SLEEP_WEEKEND_HOUR", 1, 0, 23),
        sleep_nudges_enabled=os.getenv("SLEEP_NUDGES_ENABLED", "1") == "1",
    )
