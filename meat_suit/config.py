"""Centralized config. Reads from .env."""
from __future__ import annotations

import os
import secrets
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
    # LLM: required for any surface to do anything useful.
    llm_api_key: str
    llm_base_url: str
    mind1_model: str
    mind2_model: str
    mind3_model: str

    # Storage + locale.
    database_path: Path
    timezone: str

    # Behavior switches.
    nudges_enabled: bool
    sleep_weeknight_hour: int      # 0-23, hour of day after which we'd nudge sleep
    sleep_weekend_hour: int        # 0-23, same but for Fri/Sat nights (commonly later)
    sleep_nudges_enabled: bool

    # Web server.
    web_bind_host: str             # default 127.0.0.1; set to 0.0.0.0 only behind a tunnel
    web_port: int
    cookie_secret: str             # signing key for the session cookie; never typed by hand
    web_cookie_secure: bool        # True in prod (HTTPS), False on plain localhost dev


def _generate_cookie_secret_and_persist(env_path: Path) -> str:
    """Generate a 256-bit url-safe secret used to sign session cookies.

    This is NOT a user-facing passcode. It is the cookie signing key, set
    once on first boot and never typed. Rotating it logs everyone out, which
    is exactly what you want after a leak.

    Re-scans the .env file just before writing in case another process raced
    us to it, or in case an older deploy used the legacy MEAT_SUIT_WEB_KEY name.
    """
    legacy_name = "MEAT_SUIT_WEB_KEY"
    new_name = "MEAT_SUIT_COOKIE_SECRET"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                for prefix in (f"{new_name}=", f"{legacy_name}="):
                    if stripped.startswith(prefix):
                        existing = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        if existing:
                            return existing
        except OSError:
            pass
    secret = secrets.token_urlsafe(32)
    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        with env_path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"\n# auto-generated on first boot; rotating logs you out\n"
                f"{new_name}={secret}\n"
            )
    except OSError:
        # Disk write failed (read-only FS, container, etc). The secret still
        # works for this run; it just won't survive restart.
        pass
    return secret


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

    # Cookie signing secret: auto-generated and persisted on first boot. Reads
    # the legacy MEAT_SUIT_WEB_KEY name as a fallback so old .env files keep
    # working without forcing everyone to log out on upgrade.
    cookie_secret = os.getenv("MEAT_SUIT_COOKIE_SECRET") or os.getenv("MEAT_SUIT_WEB_KEY")
    if not cookie_secret:
        env_path = Path(os.getenv("ENV_FILE_PATH", ".env")).expanduser().resolve()
        cookie_secret = _generate_cookie_secret_and_persist(env_path)

    web_bind_host = os.getenv("WEB_BIND_HOST", "127.0.0.1")
    web_port = _int_in_range("WEB_PORT", 5050, 1, 65535)
    # On localhost we serve plain HTTP, so Secure cookies wouldn't work. Behind
    # a Cloudflare Tunnel the connection is HTTPS end-to-edge, so Secure is
    # both safe and required.
    web_cookie_secure = os.getenv("WEB_COOKIE_SECURE", "0") == "1"

    return Config(
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
        web_bind_host=web_bind_host,
        web_port=web_port,
        cookie_secret=cookie_secret,
        web_cookie_secure=web_cookie_secure,
    )
