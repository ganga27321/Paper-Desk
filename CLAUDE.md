# Paper Desk — notes for Claude

**This repo is PUBLIC.** Paper trading only. Never commit anything about the owner's real money: holdings, cash,
amounts, trades, watchlists, account balances, alert levels, the ntfy topic, or anything copied from the private repo
ganga27321/the-market-guy other than the output of its `.github/scripts/desk_feed.py` (market-only feed).

- 7 traders, C$3,000 each, all books in C$ (US prices converted at USD/CAD). Six aggressive, Theo (options_safe) the one safe player.
- Playbook for the 7 AM meeting and the Saturday weekly review: `papertrade/MEETING.md`.
- State lives on the `paper-data` branch (`papertrade/state/`, gitignored on main). `paper-desk.yml` runs scans, cycles,
  the weekly PDF and deploys the dashboard to GitHub Pages; `desk-settings.yml` applies scale changes sent from the
  dashboard as a GitHub issue (owner only).
- Tests: `python -m pytest tests -q`.
- No "can I buy" verdicts, never place real orders, never force-push.
