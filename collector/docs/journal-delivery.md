# Journal delivery

For actual natural paragraphs, use the [narrative writer](narrative-writer.md). The configuration below describes the older fixed-topic transport. Set `fixed_publishing_enabled:false` to retire that entry sink while retaining its queues and receipts; personal Dot pulling and raw collection are independent.

Collection runs without GitHub. Delivery is a separate optional job that classifies retained evidence locally and sends fixed labels to a private journal. No model API key is used. The destination is pinned in code to `snackdriven/day-crumbs`, branch `main`. These publishing controls are for that repository's owner. Changing a config field cannot redirect delivery; another owner must deliberately adapt the publisher and journal layout before enabling it.

Use an installed `gh` CLI authenticated as the intended owner with private repository access and push permission. The publisher verifies privacy and uses compare-and-swap updates. Authentication and transport failures block delivery and appear as fixed status codes. No credentials belong in JSON templates or this repo.

Work and personal queues/receipts are separate. Journal days use original source timestamps with a 03:00 America/Chicago cutoff; Dot keeps its explicit source-provided day. Rendered entries mark partial coverage. Discussion of a task does not establish completion. Changed evidence or conflicting edits require review. Preserve queue databases across upgrades to retain duplicate prevention.

## Windows personal job

Complete the collector quickstart. Copy `examples/personal-summary.json.example` to the private runtime as `personal-summary.json`. Its source, queue and timezone paths must be exactly `collector.sqlite3`, `personal-summary.sqlite3` and `tzdata` under that same runtime. Keep both `enabled` and `dot_handoff_enabled` false.

The personal worker requires a private pinned timezone file in addition to the Python `tzdata` package. Activate the venv, run `python -m pip install tzdata`, then run this from `collector/` with the quickstart's `$runtime` variable. It copies installed timezone data and fills actual paths/hash in the copied configuration:

```powershell
@'
import hashlib, importlib.resources, json, pathlib, shutil, sys
runtime = pathlib.Path(sys.argv[1]).resolve()
path = runtime / 'personal-summary.json'
config = json.loads(path.read_text(encoding='utf-8-sig'))
blob = importlib.resources.files('tzdata.zoneinfo').joinpath('America/Chicago').read_bytes()
zone = runtime / 'tzdata' / 'America' / 'Chicago'
zone.parent.mkdir(parents=True, exist_ok=True)
zone.write_bytes(blob)
gh = shutil.which('gh')
if not gh:
    raise SystemExit('Install and authenticate gh first')
config.update(data_dir=str(runtime), source_db=str(runtime/'collector.sqlite3'),
    queue_db=str(runtime/'personal-summary.sqlite3'), gh_path=str(pathlib.Path(gh).resolve()),
    tzdata_dir=str(runtime/'tzdata'), tzdata_sha256=hashlib.sha256(blob).hexdigest(), enabled=False)
path.write_text(json.dumps(config, indent=2)+'\n', encoding='utf-8')
'@ | python - "$runtime"
```

This writes private setup files and makes no GitHub requests. Do not copy a live deployment's assets/config into this repo. A timezone update requires deliberately replacing that file and its hash together.

Inspect local preparation and stage a disabled task:

```powershell
python personal_summary_service.py --config "$runtime/personal-summary.json" --once --dry-run
.\install_personal_summary.ps1 -Config "$runtime/personal-summary.json" -Mode Plan
.\install_personal_summary.ps1 -Config "$runtime/personal-summary.json" -Mode Install
.\install_personal_summary.ps1 -Config "$runtime/personal-summary.json" -Mode Status
```

Dry-run can update the local queue but neither publishes nor pulls Dot. `Install` keeps delivery disabled. After checking destination and dry-run output, the intended owner explicitly enables the first real delivery with Start:

```powershell
.\install_personal_summary.ps1 -Config "$runtime/personal-summary.json" -Mode Start
.\install_personal_summary.ps1 -Config "$runtime/personal-summary.json" -Mode Status
.\install_personal_summary.ps1 -Config "$runtime/personal-summary.json" -Mode Stop
.\install_personal_summary.ps1 -Config "$runtime/personal-summary.json" -Mode Remove
```

The windowless job runs hourly while signed in and awake and at sign-in. Stop writes `personal-summary-STOP`, disables the job and waits for owned work. Remove also unregisters it; retained data remains. Reinstallation disables delivery again. Inspect `personal-summary-status.json` and receipts privately to verify an actual pass. Task registration alone does not establish delivery. Collection has separate controls.

## Work Mac and recovery

See [Mac setup](../macos/README.md) for the staged, disabled work-summary add-on. Raw work transcripts stay on the Mac; only fixed labels reach the journal. Work summaries use system/installed zoneinfo with America/Chicago available. The personal timezone pin above is a separate Windows deployment requirement.

Expired authentication requires deliberate owner sign-in. Jobs never drive a visible browser to recover. Keep workers stopped while changing bindings. Do not delete receipt queues to clear errors: resolve the fixed error code instead. Lost receipts can lose duplicate prevention or deleted-block intent. Retained runtime directories contain private data after task removal.
