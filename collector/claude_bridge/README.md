# Regular Claude account reader

Install Node.js/npm and the pinned Electron dependency from `collector/` with `npm install`. Local log collection needs neither Electron nor account login. This bridge uses internal first-party endpoints; it may need repair when the service changes.

Copy [claude.json.example](../examples/claude.json.example) to `claude.json` in your private runtime and replace all placeholders. Use the same runtime/database as `collector.json`; `package_root` is the absolute `collector` source directory, and `python` is the absolute venv interpreter. Use a new, separate `profile_dir` outside the checkout. Do not point it at your normal browser or desktop Claude profile. Keep this profile for future sign-ins; never commit or share it.

Find the installed Electron executable by running this from `collector/`:

```sh
node -p "require('electron')"
```

Run that executable with the absolute bridge script and configuration paths. In PowerShell, `&` invokes an executable path stored in a variable; in a POSIX shell quote the executable path directly. Example PowerShell after replacing these paths:

```powershell
$electron = node -p "require('electron')"
& $electron (Join-Path (Get-Location) 'claude_bridge/main.cjs') --config "$runtime/claude.json" --login
```

`--login` opens a visible first-party sign-in window. Complete sign-in yourself and close it before collection. It does not ingest messages. This is the one deliberate interactive setup step; background workers never take over the desktop to refresh a login.

Initialize the store first, then test one bounded hidden read:

```powershell
& $electron (Join-Path (Get-Location) 'claude_bridge/main.cjs') --config "$runtime/claude.json" --once
```

For a POSIX shell, assign `electron="$(node -p "require('electron')")"` and run `"$electron" "$PWD/claude_bridge/main.cjs" --config "$runtime/claude.json" --once` (or `--login`). Electron needs a supported graphical user session even when its window is hidden; headless server compatibility is not claimed.

After the bounded read, inspect `claude-status.json` privately. Set `claude.enabled` to true in `collector.json`, set `claude.electron` to that absolute executable and `claude.config` to the absolute `claude.json`. Restart with `manage.py`. Normal mode never calls show/focus. Account polls default to 60 seconds with error backoff. `authentication_required` means stop, perform explicit `--login` again, close it and restart. Do not share a profile between active Electron workers.

The bridge retains fetched trees for conversations updated after `since`; those trees can include older messages. Reads are bounded by pages and conversations. Embedded plain-text attachments can be retained; images, unsupported files and missing content remain incomplete. Cookies, tokens and authorization headers are used in the Electron session without extraction. Batches go directly through Python stdin into SQLite. STOP, lock files and metadata receipts live in the private runtime/profile. Routine diagnostics omit bodies.

Run synthetic bridge tests from `collector/` with `npm test`. Tests make no account requests.
