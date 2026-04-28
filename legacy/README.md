# legacy/

Telegram-era code, kept for one commit so the diff is easy to read, then
removable. Web app supersedes all of this.

- `bot.py` — full Telegram handler. Cron jobs, command handlers, photo intake,
  poll handlers, ReplyKeyboard. Replaced by `meat_suit/api.py` (handlers) +
  `meat_suit/scheduler.py` (cron).
- `body_check.py` — 2pm poll question/options. Data tables (`body_check_polls`,
  `status_events`) live on; the Telegram poll mechanism is gone. Web equivalent
  is a deferred v2 feature.
- `low_spoon.py` — ReplyKeyboard buttons for non-verbal status taps. Same deal:
  the data table stays, the Telegram surface is gone.

Delete this directory once the web app is solid.
