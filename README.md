# Paper Desk

Seven paper traders, C$3,000 each, all books in Canadian dollars. **Paper money only: nothing here can reach a broker.**

| Trader | Role | How the bot trades | Guardrails |
|---|---|---|---|
| Dax | Day trader, small caps, super aggressive | Gappers $1-20 up 8%+ on 3x volume; buys a break of the 15-min high on **1-minute bars, checked every 5 min** | Stop under the range low, 2R target, no entries after 3:15, flat by 3:50, 12% day-loss halt |
| Sam | Swing: news, earnings, rates, 10+ trades/week | Two pick windows a day (morning, 1 PM): council list, report plays, 20-day breakouts, uptrend pullbacks; reads the 10-yr yield | 1.5 ATR stop, 3 ATR target, 10-day time stop, skips earnings within 2 days |
| Lena | Long-term growth + dividends | Monday review of quality names (revenue +8%+, margin 15%+, US$3B+) on a pullback to a rising 50-day | 18% safety stop, sells if revenue shrinks |
| Fox | Futures & metals (sandbox) | Gold/silver/crude/Nasdaq micros: 20/50-day trend, enters on a 20-day breakout, long or short | 2 ATR stop, 4 ATR target, margin call liquidation |
| Vega | Options, super aggressive | One momentum call or put a day, 7-21 days out, steps further out of the money to fit | +100% take profit, -50% cut, 25% day-loss halt |
| Theo | Options, the safe one | Wheel: cash-secured puts on stocks under ~US$21 above their 200-day, then covered calls; idle cash parked in CASH.TO | 3% day-loss halt, TFSA-legal only |
| Kai | Crypto (sandbox) | Buckets: BTC/ETH core, news, meme, growth coins (XRP...) only while above their 20-day | Every coin has a stop: core -25%, growth -30%, news -10%, meme -20% |

Dashboard: https://ganga27321.github.io/Paper-Desk/

- A 7 AM (Toronto) meeting writes the day's plan; a fast whole-market US + Canada scan follows.
- The engine replays 5-minute bars, so fills are honest even when GitHub's schedule runs late. TFSA traders follow
  TFSA rules (long only, no margin, regular hours, whole shares, long options / covered calls / cash-secured puts).
- Style scales (0-10) drive each trader's rules and can be moved from the dashboard.
- Saturday: weekly review, scale adjustments, and C$200 to the week's biggest earner. A weekly PDF follows.

State (accounts, trades, plans, scans) lives on the `paper-data` branch. `paper-desk.yml` runs everything.
Playbook: `papertrade/MEETING.md`. Not financial advice.
