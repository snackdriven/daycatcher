# Daycatcher collector

The working collector lives here. It reads local Codex and Claude Code logs into a private SQLite store. An optional Electron worker reads regular Claude chats through its own signed-in session. Collection and fixed-label journal rendering make no model calls and need no model API key.

Cloning does not start anything. Start with local collection below; add account readers and publishing separately. The original web app has its own [guide](../docs/web-prototype.md).

## Fresh local setup

Use Python 3.11 or newer. Run these commands from `collector/` in your clone. Keep the clone at a stable path if you install startup jobs. Keep runtime data outside the checkout, in a private directory belonging to your OS user.

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
$runtime = Join-Path $env:LOCALAPPDATA 'Daycatcher\runtime'
python -m journal_collector init --data-dir "$runtime"
$config = Join-Path $runtime 'collector.json'
python -m journal_collector scan --config "$config"
python manage.py start --config "$config"
python manage.py status --config "$config"
python manage.py stop --config "$config"
```

macOS or Linux shell:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
umask 077
runtime="$HOME/.local/share/daycatcher/runtime"
python -m journal_collector init --data-dir "$runtime"
config="$runtime/collector.json"
python -m journal_collector scan --config "$config"
python manage.py start --config "$config"
python manage.py status --config "$config"
python manage.py stop --config "$config"
```

`init` writes absolute paths into `collector.json`, creates `collector.sqlite3` and sets a fixed horizon 24 hours before initialization. Inspect the generated file before collection. Change `local.since` to an aware ISO timestamp for your intended history, then restart. Widening the horizon triggers a bounded rescan; narrowing it leaves already retained records in place.

The installed `journal-collector` command is equivalent to `python -m journal_collector`. Management and installer scripts run from this source directory; pip does not install those scripts as commands.

Default roots are `~/.codex/sessions`, `~/.codex/archived_sessions` and `~/.claude/projects`. Set explicit `local.codex_roots` and `local.claude_roots` arrays to change them. An empty array disables that reader; `null` uses defaults. [Example configurations](examples/README.md) document the fields.

Local polls default to 15 seconds. `scan` runs once; `watch` stays in the foreground. `manage.py start` detaches the workers and returns after checking startup. No terminal needs to stay open. Duplicate starts are rejected. `stop` writes a graceful STOP request; an in-flight account request may finish first. If it reports `stop_requested`, inspect status again before restarting. A running process alone does not establish source coverage: inspect scan counts, errors and retained metadata.

## Start after sign-in

On Windows, activate the collector virtual environment so the installer resolves its `python.exe` and adjacent `pythonw.exe`:

```powershell
.\install_windows_startup.ps1 -Config "$config" -Mode Install
.\install_windows_startup.ps1 -Config "$config" -Mode Status
.\install_windows_startup.ps1 -Config "$config" -Mode Remove
```

This creates a limited current-user task, `Conversation Collector - <username>`, 20 seconds after sign-in. It needs no stored password. Removing the task leaves running workers and retained data alone; use `manage.py stop` too. A manual worker stop lasts until explicit start or next sign-in. Keep the source checkout and virtual environment in place.

On macOS use the [native launchd installer](macos/README.md), which stages a separate private code copy. Linux supports the CLI and detached manager; no Linux startup installer is included. None of these jobs wakes a sleeping machine. Regular Claude account reads require a network connection; local log reads do not.

## Other conversation sources

- **Regular Claude:** [bridge setup](claude_bridge/README.md). A separate profile needs one deliberate interactive sign-in. Background collection never opens a login window or drives the visible Claude app. Internal account endpoints may change.
- **ChatGPT:** [native adapter runbook](docs/native-chatgpt.md). The importer accepts exact supported Codex app tool output. A Codex app scheduled agent must supply that output; this is not a standalone ChatGPT account API or a browser scraper.
- **Dot:** [source handoff](docs/source-handoffs.md). The included receiver accepts fixed topic fragments from a separately configured private producer. It does not read Dot chat history or provision its cloud scheduler.
- **Muse:** unconnected. No reader or credential setup is shipped.

Closed Codex and Claude Code desktop apps do not prevent reading existing local logs. Closing a desktop app does not create missing logs or account access. All sources have partial coverage; unsupported content and uncertain human authorship remain incomplete.

## Journal delivery

[Publishing setup and controls](docs/journal-delivery.md) cover the separate personal Windows and work Mac jobs. They classify locally into fixed labels, then render partial context. Source timestamps use a 03:00 America/Chicago journal cutoff. Raw work transcripts stay on the Mac.

This is single-owner publishing code: the destination is pinned to **`snackdriven/day-crumbs` on `main`** in code and validation. Changing a JSON repository field does not redirect it. A new user's local collection works without that repository, GitHub authentication or publishing. Adapting publishing to another destination requires a deliberate code change and review of its journal layout and privacy contract.

Summary installation stages disabled jobs by default. Publishing requires the intended owner's authenticated `gh` executable and access to that private repository with push permission. Worker stop controls are separate from collector stop controls. No source sign-in or GitHub credentials are included in the repo.

## Retained data

The SQLite store contains plaintext messages, revisions and private source cursors. Treat the runtime directory, profile, database backups and temporary tool pages as private. Routine output omits bodies, but metadata and paths can still identify sources. Do not publish runtime files or copy them into issue reports.

```sh
python -m journal_collector preview --config "$config" --limit 10
python -m journal_collector exclude --config "$config" --message-key MESSAGE_KEY
```

`preview --text` explicitly reveals retained text. `exclude` removes the message's bodies, revisions and jobs and stores a tombstone against reimport. It does not erase source-app records, backups or previously published journal context. Journal evidence changes are flagged for review rather than silently rewriting delivered text. Preserve receipt databases when moving or backing up an installation to avoid losing delivery history.

## Development

```sh
python -m unittest discover -s tests
python -m unittest discover -s macos -p 'test_*.py'
npm install
npm test
```

Python tests and bridge tests use synthetic fixtures. `npm install` installs the pinned Electron dependency and may download its runtime; local-only collection does not require it. Tests do not sign into accounts or install live jobs. Installer preparation can be inspected separately with `--prepare-only`. Synthetic tests establish code behavior; a deployment still needs its own bounded source read and status check before claiming live coverage.

On Windows, some timezone integrity fixtures need a physical `zoneinfo.TZPATH`. Set it to the installed package before running Python tests to exercise those checks instead of skipping them. This setting supplies test fixtures only; the personal summary worker still requires its separate private timezone file and hash described in the [delivery guide](docs/journal-delivery.md).

```powershell
$env:PYTHONTZPATH = python -c "import importlib.resources; print(importlib.resources.files('tzdata').joinpath('zoneinfo'))"
python -m unittest discover -s tests
python -m unittest discover -s macos -p 'test_*.py'
```
