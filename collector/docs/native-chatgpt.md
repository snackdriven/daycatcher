# Native ChatGPT adapter

This is a Codex app integration. The Python scripts cannot authenticate to ChatGPT, list account chats or run the Codex app's native tools themselves. An explicitly configured Codex app scheduled agent supplies exact supported tool output while its native ChatGPT connection is available. A standalone API key does not replace that connection. This repository does not install that app automation.

The bounded workflow is:

1. In the Codex app, list at most 50 recent mixed chats with its native list tool. Preserve the exact JSON output privately, without reformatting or inventing records.
2. From `collector/`, run `python native_chatgpt_plan.py --config /absolute/private/runtime/collector.json --list /absolute/private/list.json --limit 5`. The planner selects recent ChatGPT chats fairly using private per-chat watermarks; it makes no source reads.
3. Have the app agent read at most ten turns for each selected ChatGPT chat with its native read tool. Keep exact output in a private temporary file. Import each page with `python import_chatgpt_native.py --config /absolute/private/runtime/collector.json --input /absolute/private/page.json --expected-thread SOURCE_ID --since AWARE_ISO --until AWARE_ISO`. Use `--dry-run` first to validate without ingesting. Replace placeholders with the actual selected ID and bounds.
4. Only after that actual import attempt, acknowledge it with `python native_chatgpt_plan.py --config /absolute/private/runtime/collector.json --ack SOURCE_ID --observed-at AWARE_ISO --status imported`. Use `incomplete` or `error` as appropriate. Only `imported` advances the successful-read watermark. The planner cannot independently prove import success.

Use canonical `collector.json` directly inside its absolute `data_dir`, with `db` exactly `collector.sqlite3` there. Tool schema mismatch must fail closed; do not transform a different export format to look like native output. The app connection and tool schema can change. Tests validate supported synthetic pages, not current account authorization.

Only supported user messages are imported. Assistant content references are unavailable through this path; attachments, truncation and uncertain provenance keep coverage partial. This bounded recent pass is not a full account archive. Temporary native pages contain private message content; delete them after ingestion according to your local retention choice and keep them out of source control.

To stop this path, disable its Codex app automation and create `native-chatgpt-STOP` in the private runtime. The importer checks that file. This does not stop local collection or journal publishing; use their own controls too. Remove the file only when deliberately resuming this path. A separate ChatGPT window is unnecessary, but the Codex app integration must be available for scheduled reads.
