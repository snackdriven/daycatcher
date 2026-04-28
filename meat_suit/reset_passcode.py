"""Clear the stored passcode so the next page load shows the setup screen.

Usage:
    python -m meat_suit.reset_passcode

Confirms before deleting. Reads DATABASE_PATH from .env so it hits the same DB
the running server uses.
"""
from __future__ import annotations

import sys

from . import db
from .config import load_config


def main() -> int:
    cfg = load_config()
    db.init_db(cfg.database_path)

    if not db.auth_has_passcode(cfg.database_path):
        print(f"no passcode set in {cfg.database_path}; nothing to do.")
        return 0

    print(f"this will clear the passcode stored in {cfg.database_path}.")
    print("the next visit to the web UI will show the setup screen.")
    confirm = input("type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("aborted.")
        return 1

    db.auth_delete(cfg.database_path)
    print("passcode cleared. restart not required; the SPA picks it up on reload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
