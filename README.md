# Daycatcher

You already told an assistant about your day. Possibly several assistants, in several windows, while doing something else entirely.

Then a journal app asks what happened today. Excellent question. There were tabs.

Daycatcher gathers conversation context from multiple assistants into a private journal. Keep talking wherever you normally talk. You don't have to pick one companion or remember to write the recap yourself.

---

## What it catches

Personal Windows and a work Mac add high-level context to the same private journal.

- **Codex and Claude Code:** hidden Python readers collect native local logs. The visible desktop apps can be closed.
- **Regular Claude chats:** a hidden Electron bridge uses its own signed-in account session, independently of the visible Claude app. Bounded account reads and internal endpoint changes limit what it can catch.
- **Work Mac:** local Codex and Claude Code collection, with a separate hourly journal job. Raw work transcripts stay on the Mac; only fixed context labels leave it.
- **ChatGPT:** an hourly Codex app automation reads supported user messages. Each pass lists at most 50 recent mixed chats, selects up to five ChatGPT chats and reads at most ten turns each. Assistant bodies aren't available through this path. Codex and its connected ChatGPT reader need to be available; a separate ChatGPT window isn't needed.
- **Dot:** its source-side producer sends fixed topic fragments through private `imports/dot/v1/YYYY-MM-DD.json` files. Its first real file and journal support were verified. Dot reports its hourly schedule saved and tested; the cloud scheduler hasn't been independently inspected.
- **Muse:** planned, still unconnected. Source access, authentication and reliable original timestamps need verification.
- **Hermes:** no journal connection. It isn't required for this to run.

That's the verified setup as of October 27, 2026. Coverage is partial, including historical catch-up. A quiet collector result doesn't mean nothing happened.

---

## How a day becomes a day

Source timestamps determine the journal day, with **03:00 as the cutoff in America/Chicago**. The conversation at 01:00 still belongs to the day you haven't gone to bed from. The calendar can cope.

Local rules turn eligible messages into fixed labels; a fixed renderer adds journal context. Dot supplies dated topic fragments, so its journal day is kept without inventing individual message timestamps.

Entries describe discussion topics, with narrow rules for directly stated human reactions when authorship and content checks support them. Dot contributes topics only. Talking about testing doesn't establish that a test passed. Assistant suggestions don't become things you supposedly did.

Rich AI-written journal prose is still ahead. The current version is deliberately modest about what it knows.

---

## The background part

The Windows collector starts after current-user sign-in. Its windowless journal job checks hourly while the PC is awake; the work Mac has its own local collector and hourly job. They don't wake sleeping machines. ChatGPT source reads have the additional Codex app dependency above.

Local classification and journal rendering use fixed rules. No new model API key is needed for the collector; source logins and GitHub authentication still are. Expired sign-in needs attention; workers don't take over the desktop to log in.

Raw text stays in private local databases, or source-side for Dot. The journal receives fixed context marked partial. Raw transcripts aren't published here or to the journal.

Separate work and personal receipts prevent duplicate paragraphs. Changes to imported evidence are flagged for review instead of quietly rewriting existing journal text. Collection and publication have separate stop controls.

---

## What's in this repo

The working multi-source collector is in [`collector/`](collector/README.md), with a local CLI, the optional Claude account bridge, background installers and synthetic tests. The [setup guide](collector/README.md#fresh-local-setup) starts local collection from a fresh clone. Cloning alone starts nothing. No model API key is needed for that path.

Source sign-ins and scheduled jobs need their own setup. Journal delivery is separate and still pinned to the owner's private `snackdriven/day-crumbs` repository. ChatGPT needs the Codex app integration; Dot needs a separately configured source producer. Their adapters are included, with those limits spelled out in the guide. Muse remains unconnected.

Daycatcher started as a four-voice chat app with a nightly journal writer. That original FastAPI/SQLite and Vite/React prototype remains here alongside the collector.

The [web prototype guide](docs/web-prototype.md) preserves its setup, commands and schedules. The [web design spec](WEB_APP_DESIGN.md) covers that earlier architecture. The prototype uses its own model endpoint and credentials; those aren't requirements of the current collector.

Existing `meat_suit` Python paths, database filename and session/environment names are kept for compatibility. The project is Daycatcher.

## License

MIT.
