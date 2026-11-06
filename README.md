# Paper Desk

Seven paper traders, C$3,000 each, all books in Canadian dollars. **Paper money only: nothing here can reach a broker.**

## Shared tools (every trader reads them, each reacts its own way)
- **Market mood** (SPY vs its 50/200-day + VIX): risk-on / neutral / risk-off; each trader has its own size cut (`regime_size`).
- **Earnings reader**: last report's gap, beat or miss, whether the gap held; next report date.
- **Cooldown**: after a stop-out, no re-entry in that symbol for the trader's cooldown days.
- **Half off at a profit**: sell half at the trader's `scale_out_r`, the rest rides with a breakeven stop.
- **Play tags**: every trade starts with its play, e.g. `[ORB+VWAP]`, so the Saturday review can score plays, not just traders.

## The seven and their unique plays
| Trader | Role | Unique plays and tools |
|---|---|---|
| Dax | Day trader, small caps, super aggressive | 1) only above VWAP 2) low-float / high-short-interest squeeze ranking 3) done after 2 losers; no entries 11:30-1:30. Plus: 1-minute bars every 5 min, opening-range breakout, flat by 3:50 |
| Sam | Swing: news, earnings, rates | 1) earnings gap-and-hold (post-earnings drift) 2) rate rotation: rising yields -> banks/energy, falling -> tech 3) breakouts need a fresh headline. Two pick windows a day |
| Lena | Long-term growth + dividends | 1) buys in thirds over three Mondays 2) dividend-raiser bonus 3) valuation guard (forward P/E, PEG) and never buys the week before a report |
| Fox | Futures & metals (sandbox) | 1) US-dollar filter for gold/silver 2) pyramids one contract into a winner 3) 3-ATR chandelier trail, no fixed target |
| Vega | Options, super aggressive | 1) skips options with implied volatility over 120% 2) calls on earnings gap-and-hold 3) half off at +100%, runner to +300% or breakeven |
| Theo | Options, the safe one | 1) no put across an earnings date 2) strike from the stock's own volatility (~20% assignment odds) 3) risk-off: half the cash, strikes 1.5x further. Idle cash in CASH.TO |
| Kai | Crypto (sandbox) | 1) Fear & Greed: buys BTC/ETH on extreme fear, no new memes on extreme greed 2) alt coins only while ETH beats BTC 3) trailing stops on news (12%) and meme (18%) coins |

Dashboard: https://ganga27321.github.io/Paper-Desk/

- A 7 AM (Toronto) meeting writes the day's plan; a fast whole-market US + Canada scan follows.
- The engine replays 5-minute bars, so fills are honest even when GitHub's schedule runs late. TFSA traders follow
  TFSA rules (long only, no margin, regular hours, whole shares, long options / covered calls / cash-secured puts).
- Style scales (0-10) drive each trader's rules and can be moved from the dashboard.
- Saturday: weekly review, scale adjustments, and C$200 to the week's biggest earner. A weekly PDF follows.

State (accounts, trades, plans, scans) lives on the `paper-data` branch. `paper-desk.yml` runs everything.
Playbook: `papertrade/MEETING.md`. Not financial advice.
