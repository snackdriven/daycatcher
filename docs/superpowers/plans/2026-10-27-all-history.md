# All Available History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Tasks have explicit ownership and checkbox checks below.

**Goal:** Safely ingest and journal all accessible historical human context while live collection and personal/work writing continue.

**Architecture:** Reuse canonical source identities and existing strict adapters. Add history-only checkpoints and transaction-protected preservation of complete records, then bounded account and day frontiers. A source-local Mac coordinator supplies controlled descriptions; the existing native writer consumes pending historical days without altering old prose.

**Tech Stack:** Python 3.11+ stdlib/SQLite/unittest; existing Electron/CJS Claude bridge; native Codex app tools; existing current-user Windows/Mac process controls.

**Spec:** `../specs/2026-10-27-all-history.md`

## Global Constraints

- No UI control, credential extraction, new model keys, Tina messaging or raw work uploads.
- Work uses `controlled-work-v1`; canonical databases, exclusions, existing complete messages, live cursors and receipts remain intact.
- Fixed 16 MiB local scan budget per pass; independent history locks/STOP; original aware timestamps and 03:00 America/Chicago day cutoff.
- No source instructions executed, no title-derived facts, no undated or uncertain-origin records promoted.
- Preserve unowned prose and exact owned paragraph prefixes. New paragraphs need fresh support, semantic review, exact remote readback and retry reconciliation.

## Review Focus

1. A stale historical copy races a fresh live import: the complete canonical row must remain unchanged under the write transaction.
2. Exclusion during a historical pass: no tombstoned body may return through history.
3. A worker dies after import or accepted publish: checkpoints and immutable candidates reconcile without duplicate effects.
4. Oversize sources, more than 32 work moments, unsupported native schemas and missing discovery end proof: record terminal gaps/capacity outcomes rather than fabricate or loop forever.
5. Multiple account workers or a source/profile mismatch: refuse concurrent ambient-profile use and preserve all prior checkpoints.

---

### Task 1: Historical ingestion lane

**Files:** modify `collector/journal_collector/store.py`, `local.py`, `cli.py`; create `collector/history_collect.py`, `journal_collector/history_collect.py`; tests in `collector/tests/test_history_collect.py` plus focused existing Store/local tests.

**Interfaces:** `Store.ingest_batch(batch, *, preserve_existing_complete=False)`; `collect_local(..., cursor_namespace=None, preserve_existing_complete=False)`; `history_collect.py --config ABS --once|--watch`.

- [x] Write failing tests for separate live/history cursor keys, atomic preservation of every field/revision/job of complete canonical rows, conflict counts, exclusions and checkpoint retries.
- [x] Run focused tests and confirm new assertions fail.
- [x] Implement optional trusted historical policy inside `BEGIN IMMEDIATE`; keep ordinary behavior unchanged. History cursor names must not enter message provenance's canonical file identity.
- [x] Implement a private durable file queue with bounded small-file batches, independent lock/STOP, no live configuration mutation, metadata-only status and resumable discovery/EOF outcomes.
- [x] Run focused tests, review diff and commit after task review.

### Task 2: Historical account frontiers

**Files:** create `collector/native_chatgpt_history.py` and tests; extend `claude_bridge/backfill_logic.cjs`, `backfill.cjs` and tests; use Task 1 trusted historical ingest CLI flag.

**Interfaces:** native helper accepts exact schema4 active metadata, archived metadata and exact schema1 pages into a separate history frontier; emits at most five bounded reads per plan and acks actual import results only. Claude inventory accepts a bounded start offset and returns next offset/end proof; no 90-row pretend completion.

- [x] Test old chats outside recent planner horizons, pagination resume, unsupported/truncated records, stale imports, exact source binding, end proof and untouched live planner checkpoints.
- [x] Implement strict native historical import through the transaction-preserving Store lane and a separate frontier, not the recent planner.
- [x] Add bounded Claude metadata pagination and historical ingestion mode. Keep account/profile locks and source binding; never copy profiles or extract cookies.
- [x] Verify synthetic account tests; run one controlled native page and one hidden Claude inventory only after reviewed source integration.

### Task 3: Work historical coordinator

**Files:** create `collector/work_history_service.py`, `journal_collector/work_history.py`, `macos/history_install.py` and focused tests; modify `work_narrative_service.py` only for explicit bounded historical pagination/capacity handling.

**Interfaces:** canonical Mac `collector.json`, existing model/Ollama/gh settings, Task 1 historical importer, unchanged controlled ledger. Independent history queue stores only private day/status state; shared model lock gives live work priority. Publisher uses the existing controlled wire and immutable outbox.

- [x] Test current-day priority, independent STOP, stopped/reboot resume, complete-day detection, paged large days, oversized records and 32-moment capacity outcomes.
- [x] Implement bounded source-local discovery/scan/model coordinator, with no raw outbound diagnostics and no reset of controlled/legacy ledgers.
- [x] Add owned disabled-first launchd staging, exact argv/hash validation, current-user startup and a single pinned setup command with metadata-only inventory/readback.
- [x] Test preserving raw collector and all prior code/data controls; require real controlled handoff proof before live start.

### Task 4: Historical journal queue and real acceptance

**Files:** create `collector/narrative_history_plan.py` and tests; update `docs/narrative-writer.md`; private runtime history receipts/candidates remain outside checkout.

**Interfaces:** canonical personal config; eligible source day/fingerprint discovery; up to two historical days per native writer pass in addition to current/previous days; explicit prepared/reviewed/published/unchanged/gap ack outcomes.

- [x] Test older dates, late new evidence reopening, unchanged fingerprints, excluded/undated evidence, manual edits/deletion and no premature success acknowledgment.
- [x] Implement fair metadata-only day planning and acks; native writer still owns factual prose review and publication.
- [x] Update the existing writer automation's prompt while preserving its schedule/model/notification settings; no duplicate schedule.
- [x] Start personal historical collection, process a real older-day batch, independently review and verify saved prose/retry/no unrelated edits.
- [x] Run full Python/CJS/Mac portable tests and whole-branch review. Integrate reviewed source; provide one source-local Mac command; report exact coverage/progress and remaining gaps.

## Ledger

- Authorization: user chose “All available history”; earlier checklist/parallel execution and no-PC-takeover constraints persist.
- Ruling: use canonical shared Store with separate historical cursor namespace, not a second source database; atomic preservation avoids stale-copy overwrite and identity/exclusion drift.
- Ruling: all accessible history is bounded by actual adapter access and provenance. No timestamp or account-completeness inference from titles, file names or a recent list.
- Baseline: isolated checkout on `codex/history-backfill`, 335 core tests pass. Native worktree tool cannot target this projectless task's nested checkout; native attempt returned “Not a git repository,” so standard ignored Git worktree fallback is used.

- Delivery: reviewed source integrated and pushed as dae1b16810133b4c7aa54af9872132f1eecfd5ea. Core 385, portable Mac 62, CJS 27, setup wrapper 5 tests pass; final source STOP regression passes.
- Real acceptance: historical local inventory traversed 123 files (117 EOF, 6 explicit gaps); hidden Claude inventory exposed 90 chats, 87 non-probe bodies processed in bounded batches with live workers restored. Native ChatGPT historical frontier discovered 54 exposed chats, imported five pages, and retains partial active coverage.
- Journal acceptance: October 24 and 25 gained two independently reviewed paragraphs each; exact publication/readback/retry succeeded, original prose preserved. October 25's two untouched legacy topic spans archived and removed. Two day receipts acknowledged; 89 dates remain pending, with two historical dates configured per existing hourly native writer pass.
- Work handoff: pinned one-command historical setup saved and exact-byte verified in the existing Gmail draft. Actual Mac execution and older work delivery remain pending user Terminal action; no claim of live historical Mac installation.
