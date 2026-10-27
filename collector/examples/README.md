# Configuration templates

These are synthetic templates. Copy only needed files into a private runtime outside the checkout; remove `.example` and replace every `ABSOLUTE_*` value with a canonical absolute path. JSON backslashes need escaping; forward slashes work on Windows. Prefer `init` in the [quickstart](../README.md) for a ready local collector configuration.

| File | Required binding |
| --- | --- |
| `collector.json` | `data_dir` is private runtime, `db` is its `collector.sqlite3` |
| `claude.json` | Same runtime/database; `package_root` is the checkout's `collector` directory; `python` is the venv interpreter; `profile_dir` is a separate private profile |
| `personal-summary.json` | Must be directly in `data_dir`; source/queue/timezone paths are exactly `collector.sqlite3`, `personal-summary.sqlite3`, `tzdata` under that directory |
| `summary.json` | Work summary; source/queue are `collector.sqlite3` and `work-summary.sqlite3` under `data_dir` |

Example dates are synthetic. Set your intended aware ISO horizon. Local roots `null` selects default home logs; `[]` disables a reader. Both publishing templates remain disabled and use the code-enforced owner destination. Unresolved paths and the placeholder timezone hash must fail validation. See [delivery setup](../docs/journal-delivery.md).
