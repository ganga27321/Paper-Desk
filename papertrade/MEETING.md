# Paper Desk: the 7 AM meeting (playbook for the morning Claude session)

Seven paper traders, C$3,000 each (all books in Canadian dollars), paper money only. The meeting runs once each weekday at about 7:00 AM Toronto
time. All seven may take advice from the Portfolio Council and from each other; the plan is shared on ntfy and the
dashboard. The scanner runs right after, focused on the tickers the meeting tabled.

## Steps
1. Clone the desk's state: `git clone --quiet --branch paper-data --single-branch "$(git remote get-url origin)" papertrade/state`
   (or `git -C papertrade/state pull`). Accounts, trades, plans, scans.
2. Refresh the market feed from the private repo (ganga27321/the-market-guy; attach it with add_repo if needed, read
   access is enough): `python <market-guy>/.github/scripts/desk_feed.py papertrade/state/feed.json`. It keeps market
   facts only (headlines, macro events, council market points, rule-based play tickers). **Never copy anything else
   from the private repo into this public one**: no holdings, cash, amounts, trades, watchlist or account names.
3. `pip install -r requirements.txt` if needed, then `python -m papertrade context > /tmp/ctx.json` and read it:
   market snapshot, each trader's cash/positions/recent trades, the feed (`council_latest`, `reports_market`,
   `reports_insights`), yesterday's scanner lists and yesterday's plan.
4. Quick news check (WebSearch, 2-3 searches): pre-market movers, macro events today (jobs, CPI, Fed, earnings),
   crypto and gold headlines.
5. Hold the meeting: each trader speaks in character (see `traders.yaml`: style, rules, tfsa). They can cite the
   council or another trader ("Sam: Lena's NVDA thesis + Vega's call idea..."). Keep it decisive.
6. Write `papertrade/state/plans/<YYYY-MM-DD>.json` (schema below), validate with
   `python -m papertrade.meeting validate papertrade/state/plans/<date>.json`, fix anything it reports.
7. Commit the plan and the feed on `paper-data` and push (`git -C papertrade/state add plans feed.json && git -C
   papertrade/state commit -m "Plan <date>" && git -C papertrade/state push origin HEAD:paper-data`). That push
   shares the plan on ntfy and starts the scanner and the first engine cycle (paper-desk.yml).

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
Keep it to one pass; it is the last session of the user's usage week.
1. Clone/pull `papertrade/state` as in step 1, refresh the feed (step 2), then `python -m papertrade context > /tmp/desk.json`
   and read each trader's `week` block, scales, positions and trades.
2. Write `papertrade/state/reviews/<YYYY-Www>.json` (ISO week of today, e.g. 2026-W45):
   `{"week": "2026-W45", "summary": "2-3 sentences on the desk's week",
     "weekly_review": {"<trader id>": ["2-3 insights: what worked, what didn't, what to change"], ...all 7},
     "adjustments": {"<trader id>": {"scales": {"<knob>": 0-10}, "sectors": {"<Industry>": 0-10}, "why": "..."}}}`
   Knobs: aggression, frequency, holding, deployment, stop_width, target. At most 3 scales per trader, usually 1-2
   steps. Theo (options_safe) stays the safe player: aggression 4 or below. The other six stay aggressive. Don't undo a
   change the user made this week (history entries `by: you`) without saying why.
3. `python -m papertrade.meeting validate <file>`, then commit that file (and feed.json) on paper-data and push. The
   push makes GitHub apply the adjustments, pay the C$200 bonus to the week's biggest earner, build the weekly PDF
   and send it to the user's phone.
