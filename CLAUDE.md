# Paper Desk — notes for Claude

**This repo is PUBLIC.** Paper trading only. Never commit anything about the owner's real money: holdings, cash,
amounts, trades, watchlists, account balances, alert levels, the ntfy topic, or anything from the owner's private
repos. The desk is independent: it reads nothing from them, and any security is fair game. Nothing outside this repo
may be copied into `papertrade/state` (the state's `.gitignore` blocks the old `feed.json`).
- Meetings are time-aware: read `clock` in the context first; events already released are reported with actual numbers.

- 7 traders, C$3,000 each, all books in C$ (US prices converted at USD/CAD). Six aggressive, Theo (options_safe) the one safe player.
- Playbook for the 7 AM meeting and the Saturday weekly review: `papertrade/MEETING.md`.
- State lives on the `paper-data` branch (`papertrade/state/`, gitignored on main). `paper-desk.yml` runs scans, cycles,
  the weekly PDF and deploys the dashboard to GitHub Pages; `desk-settings.yml` applies scale changes sent from the
  dashboard as a GitHub issue (owner only).
- Tests: `python -m pytest tests -q`.
- No "can I buy" verdicts, never place real orders, never force-push.
