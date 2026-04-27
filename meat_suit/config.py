"""Centralized config. Reads from .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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


def load_config() -> Config:
    db_path = Path(os.getenv("DATABASE_PATH", "./data/meatsuit.db")).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Config(
        telegram_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_owner_id=int(_required("TELEGRAM_OWNER_ID")),
        openai_api_key=_required("OPENAI_API_KEY"),
        mind1_model=os.getenv("MIND1_PERSONA_MODEL", "gpt-4o-mini"),
        mind2_model=os.getenv("MIND2_SCRIBE_MODEL", "gpt-4o-mini"),
        mind3_model=os.getenv("MIND3_WEAVER_MODEL", "gpt-4o"),
        database_path=db_path,
        timezone=os.getenv("TIMEZONE", "America/Chicago"),
        nudges_enabled=os.getenv("NUDGES_ENABLED", "1") == "1",
    )
