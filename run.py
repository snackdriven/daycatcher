"""Entry point: ``python run.py``.

Boots the FastAPI app via uvicorn on the host/port configured in .env. On
first boot the config layer auto-generates MEAT_SUIT_COOKIE_SECRET (the
cookie signing key, never typed) and writes it to .env. The user-facing
passcode is set in the browser at first visit.
"""
from __future__ import annotations

import sys

import uvicorn

from meat_suit import db
from meat_suit.config import load_config


def main() -> None:
    cfg = load_config()
    host = cfg.web_bind_host
    port = cfg.web_port

    # Make sure schema is current before we print the boot hint, so the
    # passcode-check below doesn't crash on a missing auth table.
    db.init_db(cfg.database_path)

    print(f"daycatcher listening on http://{host}:{port}", file=sys.stderr)
    if not db.auth_has_passcode(cfg.database_path):
        print(
            f"first-run: open http://127.0.0.1:{port}/ to pick a passcode.",
            file=sys.stderr,
        )
    else:
        print(
            f"open http://127.0.0.1:{port}/ to log in.",
            file=sys.stderr,
        )

    uvicorn.run(
        "meat_suit.api:app",
        host=host,
        port=port,
        log_level="info",
        # No reload in production; dev users can pass --reload via the CLI directly
        # using `python -m uvicorn meat_suit.api:app --reload` if they want it.
        access_log=False,
    )


if __name__ == "__main__":
    main()
