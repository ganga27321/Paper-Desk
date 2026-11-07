# Paper Desk: the morning meeting (playbook for the morning Claude session)

Seven paper traders, C$3,000 each (all books in Canadian dollars), paper money only. The desk is **independent**: it
reads nothing from the owner's private repo or plans. Any listed security is fair game (US and Canadian stocks and
ETFs, options, futures, crypto) within each trader's own rules. The meeting runs each weekday morning; the plan is
shared on ntfy and the dashboard, and the scanner then focuses on the tickers the meeting tabled.

## Steps
1. Clone the desk's state: `git clone --quiet --branch paper-data --single-branch "$(git remote get-url origin)" papertrade/state`
   (or `git -C papertrade/state pull`). Accounts, trades, plans, scans.
2. **Clock first.** `pip install -r requirements.txt` if needed, then `python -m papertrade context > /tmp/ctx.json` and
   read `clock` before anything else: the time in New York, whether the market is pre-market, open or closed, and how
   long to the open or close. Everything below is written for that moment, not for a generic 7 AM.
3. **Today's calendar, with results.** Search for today's economic calendar and earnings (US and Canada). For every
   event whose time has already passed (jobs report, CPI, Fed speakers, earnings before the open...): search for the
   **actual result**: the number, the forecast it beat or missed, and how futures/stocks reacted since. Only events still
   ahead are described as "could go either way". Write them all into `events` (schema below); validation refuses a plan
   that calls a released number "upcoming".
4. **Market check (2-3 searches):** pre-market (or intraday, if open) movers and why, rates and the dollar, gold/oil,
   crypto. If the market is already open, use where prices are now, not where they were before the open.
5. Read the rest of the context: each trader's cash/positions/recent trades, scales, yesterday's scanner lists and plan.
6. Set the day's guardrails (the bots trade alone all day inside them, no Claude needed):
   - `levels`: for every ticker the traders table, a `stop` (required), optional `target` and `no_buy_above`, in the
     instrument's own currency. The engine never lets a long on that symbol sit below the stop and refuses entries
     above `no_buy_above`.
   - `limits`: per trader, tighten when the market is rough: `max_day_loss_pct`, `max_trades_day`. Defaults are in
     `traders.yaml` `guards`.
   - `news_tickers`: up to 8 tickers moving on real news today (Sam reads them).
   - Every stock, future and crypto order needs a `stop`. Validation fails without one.
7. Hold the meeting: each trader speaks in character (see `traders.yaml`: style, rules, tfsa), reacting to the actual
   numbers. They can cite each other ("Sam: Lena's NVDA thesis + Vega's call idea..."). Keep it decisive.
8. Write `papertrade/state/plans/<YYYY-MM-DD>.json` (schema below), validate with
   `python -m papertrade.meeting validate papertrade/state/plans/<date>.json`, fix anything it reports.
9. Commit the plan on `paper-data` and push (`git -C papertrade/state add plans && git -C papertrade/state commit -m
   "Plan <date>" && git -C papertrade/state push origin HEAD:paper-data`).
10. If something big lands after the plan is written (a number released while you work), update `events` and the
    affected orders, validate again and push again.

## During the day (no Claude)
GitHub runs the desk every 15 minutes in market hours (crypto and futures every 2 hours): fills on new bars, stops and
targets, time stops, the day-loss halt, new autopilot entries inside the morning's levels and limits. Every run that
trades sends one phone note (fills, exits with P/L, halts). 4:05 PM ET: the closing scoreboard.

## The week's rhythm
- **Weekdays:** this 7 AM meeting (plan only; no scale changes here).
- **Saturday, in the Portfolio Council session** (the last session of the user's usage week, before the 18:00 UTC
  reset): the Paper Desk weekly review. All 7 get insights on their week, their style scales are adjusted, and the
  week's biggest dollar earner gets C$200. Steps are below ("Weekly review"). If that
  session is skipped, a Sunday GitHub job still pays the bonus and builds the PDF, with no scale changes.
- **Monday's meeting** names last week's winner (`bonuses` in the context) and reads the review (`reviews/`).
- The user can move any scale from the dashboard (`by: you` in the history). Never undo a user change without saying why.

## Rules the meeting must respect
- TFSA traders (day, swing, long, options_aggr, options_safe): long only, no margin, regular hours only, whole shares,
  options only as long calls/puts, covered calls (`covered: true`) or cash-secured puts (`cash_secured: true`).
- Sandbox traders (futures, crypto) may short / use real coins and futures; label "not possible in your TFSA".
- Prices in orders are in the instrument's own currency (US$ for US stocks, futures and crypto, C$ for .TO); the
  engine converts everything to C$.
- All money can be put to work when the setups are good (deployment scale 10 for the six aggressive traders).
- Every order needs a `why` that a beginner can read (what, why, how much).
- No order is ever sent to a real broker. This is paper trading.

## Plan schema
```json
{
 "date": "2026-11-09",
 "market_view": "2-3 sentences: what matters today",
 "tabled": ["NVDA", "SOUN", "GC=F"],
 "events": [{"time_et": "08:30", "name": "US jobs report (October)", "status": "released",
             "actual": "+142k vs +110k expected, unemployment 4.1%", "reaction": "Nasdaq futures +0.8% after the print"},
            {"time_et": "14:00", "name": "Fed speaker", "status": "upcoming"}],
 "news_tickers": ["NVDA", "SOUN"],
 "levels": {"NVDA": {"stop": 168, "target": 205, "no_buy_above": 190}, "GC=F": {"stop": 3950}},
 "limits": {"day": {"max_day_loss_pct": 8, "max_trades_day": 4}, "options_aggr": {"max_day_loss_pct": 20}},
 "shared_notes": ["Council says...", "Sam and Vega agree on..."],
 "traders": {
  "day":   {"view": "...", "watch": ["SOUN", "RGTI"],
            "orders": [{"symbol": "SOUN", "kind": "stock", "entry_type": "stop", "price": 12.40, "stop": 11.80,
                        "target": 13.60, "risk_pct": 3, "why": "..."}]},
  "swing": {"view": "...", "orders": [{"symbol": "AMD", "kind": "stock", "entry_type": "market", "stop": 380, "target": 450, "why": "..."}]},
  "long":  {"view": "...", "orders": []},
  "futures": {"view": "...", "orders": [{"symbol": "MGC", "kind": "future", "side": "long", "entry_type": "stop",
              "price": 4010, "stop": 3950, "target": 4130, "qty": 1, "why": "..."}]},
  "options_aggr": {"view": "...", "orders": [{"symbol": "NVDA", "kind": "option", "right": "C", "action": "BUY_TO_OPEN",
                   "dte": [7, 21], "otm_pct": 0.03, "why": "..."}]},
  "options_safe": {"view": "...", "orders": [{"symbol": "PFE", "kind": "option", "right": "P", "action": "SELL_TO_OPEN",
                   "cash_secured": true, "dte": [25, 45], "otm_pct": 0.07, "contracts": 1, "why": "..."}]},
  "crypto": {"view": "...", "orders": [{"symbol": "SOL-USD", "kind": "crypto", "pct": 20, "stop": 150, "target": 210,
             "why": "News bucket: ..."}]}
 }
}
```
Orders are optional: with none, a trader runs on its own rules (autopilot) using the scanner. `tabled` tickers are
scanned first and every trader sees them.

## Weekly review (Saturday, after the council sitting)
A week listed in `bonus_hold` (traders.yaml) pays no bonus; its C$ rolls into the next paid week. The review and scale adjustments still happen.
Keep it to one pass; it is the last session of the user's usage week.
1. Clone/pull `papertrade/state` as in step 1, then `python -m papertrade context > /tmp/desk.json`
   and read each trader's `week` block, scales, positions and trades.
   Score the plays too: every trade's `why` starts with its play tag (e.g. `[Earnings gap-and-hold]`); say which plays
   made and lost money for each trader and tune the scales toward the ones that work.
2. Write `papertrade/state/reviews/<YYYY-Www>.json` (ISO week of today, e.g. 2026-W45):
   `{"week": "2026-W45", "summary": "2-3 sentences on the desk's week",
     "weekly_review": {"<trader id>": ["2-3 insights: what worked, what didn't, what to change"], ...all 7},
     "adjustments": {"<trader id>": {"scales": {"<knob>": 0-10}, "sectors": {"<Industry>": 0-10}, "why": "..."}}}`
   Knobs: aggression, frequency, holding, deployment, stop_width, target. At most 3 scales per trader, usually 1-2
   steps. Theo (options_safe) stays the safe player: aggression 4 or below. The other six stay aggressive. Don't undo a
   change the user made this week (history entries `by: you`) without saying why.
3. `python -m papertrade.meeting validate <file>`, then commit that file on paper-data and push. The
   push makes GitHub apply the adjustments, pay the C$200 bonus to the week's biggest earner, build the weekly PDF
   and send it to the user's phone.
