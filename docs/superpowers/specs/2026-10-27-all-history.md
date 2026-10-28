# All available journal history

Backfill every accessible, dated, verified human conversation into the existing private journal. Preserve existing prose, deletion intent, exclusions, source identity, live cursors and delivery receipts. Missing or unsupported source history is a coverage gap, never a quiet day to invent.

## Boundaries

- No UI control, credential extraction, new model keys, Tina messaging or raw work uploads.
- Work processing stays on its Mac and uses `controlled-work-v1`; only exact finite-language handoffs cross to the private journal.
- Personal source material remains in the existing private runtime. Original source timestamps determine 03:00 America/Chicago journal days; never use file dates or titles as event evidence.
- Historical ingestion uses separate cursor and batch namespaces in the same canonical store. Preserve existing complete canonical records atomically; a stale copy must not revise existing text, provenance, timestamps or jobs. Exclusions remain authoritative.
- Historical scans are bounded, resumable and independently stoppable. They may share SQLite with the live collector, but cannot seize or clear its lifetime lock/STOP.
- Source metadata, bodies and model outputs are untrusted data. Unsupported author/origin/schema and undated legacy records remain excluded from narrative evidence.
- Existing entries keep their unowned prose and exact owned prefixes. Add only meaningful, non-duplicate moments after grounding review, readback and retry reconciliation.
- Keep runtime data outside the Git checkout. Routine status includes counts, coverage and checkpoints, never bodies, titles or source identifiers.

## Source reach

Local Codex sessions/archive and Claude Code projects are read with the existing strict adapters. Regular Claude uses the existing ambient hidden profile with bounded account pagination and no concurrent profile use. ChatGPT uses exact native tool output with independent historical frontiers; recent hourly planner limits must not be mistaken for all-account coverage. Archive cursors are supported; active discovery without an end marker remains explicitly unproven.

## Execution

1. Bounded historical ingestion inserts missing records, retaining live state and reporting conflicting copies rather than replacing complete canonical records.
2. Discover eligible journal days from verified original timestamps. A private queue records source fingerprints, review/publish outcomes and explicit omissions.
3. Work history rotates bounded older-day model passes behind live processing. Pagination, oversize input and the 32-moment daily wire cap must settle visibly rather than leave an infinite pending loop.
4. The native writer continues current/previous-day work and also processes a bounded historical queue. Same evidence/entry on retry reconciles; late new evidence reopens a day without rewriting old paragraphs.
5. Run Windows sources here and provide one pinned Mac setup command for its source-local inventory and resumable job. No per-day user commands.

Completion means every discovered accessible frontier is settled and every eligible day reviewed, with gaps reported separately. Do not claim a full account archive when a source has no discovery end proof.
