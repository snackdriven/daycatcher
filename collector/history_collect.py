"""Headless historical collector entry point."""
from journal_collector.history_collect import load_config, main, run_once

if __name__ == '__main__':
    raise SystemExit(main())
