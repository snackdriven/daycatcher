# Controlled work handoffs

The work model chooses finite slots instead of writing transferable prose. `work_language.py` owns the permitted operations, purposes, reactions and sentence templates. `local_work_model.py` accepts only those slots, renders the description locally and obtains a separate grounding review against the human source. Names, project labels, URLs, quotations and arbitrary counts have no output slot.

The existing v1 wire keys are retained. Both `build_handoff` and `validate_handoff` require exact controlled-language membership for each moment's kind. A legacy free-form description fails even when it claims `sanitized: true`. This proves the literal output vocabulary; it does not prove a model chose a truthful operation or that a finished journal reads well. Those require grounding and paragraph review.

Examples of permitted descriptions include:

- `I wanted to review open changes to prepare for a release.`
- `I discussed investigating failed checks.`
- `I felt frustrated about reproducing reported failures.`

Strong action and reaction claims also need conservative literal support checks. Requests and future plans cannot establish completed actions. A reaction must be explicitly present in the human source. Generic work without a specific operation is reserved for an explicit reaction; unknown activities abstain.

## Stopped migration

Stop the owned Mac producer before upgrading. The installer validates a disabled, unlocked legacy bundle and its file hashes before replacing code. Current bundles use version 2 settings with policy `controlled-work-v1` and require the language module. Legacy settings cannot start a producer or pass current handoff proof.

With `work-narrative-STOP` still present, run the staged worker with its existing configuration, exact model and Ollama executable plus `--quarantine-legacy`. This preserves the original `work-narrative/state.json` bytes, evidence indexes and delivery records. It writes only a local hash/count quarantine marker and a separate `state-controlled-v1.json` for the new policy. Repeating the migration never resets that new state.

The controlled worker never replays a legacy payload. It reprocesses eligible current and previous journal days under the new policy, preserving source databases, cursors and exclusions. New inference requires a matching quarantine marker when legacy state exists. A changed legacy ledger or conflicting controlled state fails closed.

Inspect synthetic model output, a real controlled handoff and its private remote readback before starting the hourly job. Keep the Windows `work-handoff-STOP` in place until receiver validation and actual paragraph review pass. Personal writing can continue while work is paused. Never clear another owner's STOP or restart a stopped job implicitly.
