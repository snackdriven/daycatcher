# macOS background jobs

Run from the cloned `collector/` directory on the Mac. Python 3.11+ is required. The installer copies Python modules to `~/Library/Application Support/ConversationCollector/code`, with private runtime files in its sibling `runtime/`. The source modules and `macos/` scripts must be present together; copying only the installer is insufficient. It uses current-user launchd, no desktop control or root service.

Prepare and inspect before starting:

```sh
python3 macos/install.py install --prepare-only
```

The JSON result gives exact code/config/plist paths. Inspect its `collector.json` and set the fixed `local.since` horizon and roots deliberately. Preparation defaults to the preceding 24 hours, local Codex and Claude Code roots, and Claude account collection disabled. It creates no live launchd job. Use an explicit stable Python path with `--python /absolute/path/python3` if needed. Keep that interpreter installed.

```sh
python3 macos/install.py install
python3 macos/install.py status
python3 macos/install.py stop
python3 macos/install.py start
python3 macos/install.py uninstall
```

Live `install` starts local collection and registers `com.snackdriven.conversation-collector` for the signed-in user. Status reports retained counts and worker health. Stop unloads the job and writes STOP, with graceful wait for an in-flight scan. Uninstall removes the launchd definition while retaining private code/data. Stop before reinstalling code beneath a worker. These commands require the current user's macOS graphical login session; portable prepare-only tests do not prove live launchd behavior.

## Optional hourly work journal

This is an add-on to the installed local collector, with a separate queue and controls. Read [journal delivery](../docs/journal-delivery.md) first: the destination is pinned to the owner's private `snackdriven/day-crumbs` repository. `gh` must already be installed and authenticated for that owner's private read/push access. Zoneinfo must provide America/Chicago; install Python's `tzdata` package in the selected interpreter if the system does not provide it.

```sh
python3 macos/summary_install.py install --prepare-only
python3 macos/summary_install.py install
python3 macos/summary_install.py status
```

Both installation modes stage disabled delivery. `--gh /absolute/path/gh` and `--python /absolute/path/python3` select stable executables. Preparation copies the work-summary modules and manager into the existing private code tree, writes canonical `runtime/summary.json`, and stages `com.snackdriven.work-summary-sync` disabled. It does not copy transcripts to the repo. Inspect the config and run a local dry-run with the staged interpreter/code:

```sh
base="$HOME/Library/Application Support/ConversationCollector"
python3 "$base/code/summary_service.py" --config "$base/runtime/summary.json" --once --dry-run
```

After reviewing that bounded result, the intended owner explicitly starts delivery:

```sh
python3 macos/summary_install.py start
python3 macos/summary_install.py status
python3 macos/summary_install.py stop
python3 macos/summary_install.py uninstall
```

Start enables hourly delivery and an immediate launch. Jobs run while the Mac is awake; they do not wake it. Stop writes `summary-STOP`, disables/unloads the job, and preserves receipts. Uninstall also removes its plist; collector/data remain. Source collection continues independently. Confirm actual `summary-status.json` counts and journal receipts before claiming delivery. Reinstalling the summary stages it disabled again.
