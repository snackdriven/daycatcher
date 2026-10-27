# Dot handoff and remaining sources

Dot is a separate producer/receiver connection. The included receiver reads only private `imports/dot/v1/YYYY-MM-DD.json` files from the owner's pinned journal repository. It does not read Dot history, create credentials or install Dot's cloud automation. The producer needs its own source-side authentication, a reliable dated journal-day assignment and a separately configured schedule. That cloud-side producer is not shipped here.

Each file is bounded to 64 KiB, version 1, with an exact matching day and at most 17 fragments. Fragments contain fixed allowed personal topic codes, a unique 32-character lowercase hexadecimal fragment ID, their day and `coverage: "partial"`. Reactions and raw prose are rejected. IDs must remain stable for already issued evidence; do not generate a new ID on every retry. Source fragments are summaries, without invented original-message timestamps.

A synthetic example for shape only:

```json
{"version":1,"day":"2026-01-01","fragments":[{"fragment_id":"00000000000000000000000000000001","day":"2026-01-01","facts":["topic_planning"],"coverage":"partial"}]}
```

The receiver validates topic codes against `journal_collector/personal_codes.py`. Keep handoffs in the intended private repository only. Changed or invalid prior evidence is quarantined for review rather than silently rewritten. Receipt history prevents duplicate journal prose.

Complete the [personal summary setup](journal-delivery.md), including its canonical timezone binding and `gh` access, before receiver use. From `collector/`, inspect bounded reads without ingestion/publication:

```sh
python pull_dot_handoff.py --config /absolute/private/runtime/personal-summary.json --days 3 --dry-run
```

Dry-run still needs authenticated private GitHub reads. A real receiver pass omits `--dry-run`; it imports source fragments without publishing a journal itself. To have the personal worker pull automatically before its enabled delivery passes, set `dot_handoff_enabled` true in private `personal-summary.json`. It defaults false; `dot_handoff_days` defaults 3 and accepts 1 through 14.

Create `dot-summary-STOP` in the private runtime to stop pulls, and disable the source-side producer's schedule separately. Personal summary Stop blocks its scheduled pull/delivery path but leaves local collectors independent. Do not infer that the remote cloud scheduler is healthy from a successful receiver request; verify it at the source.

Muse is unconnected. No verified source reader, authentication flow or original-timestamp contract is implemented. Hermes has no journal connection and is not required by these workers.
