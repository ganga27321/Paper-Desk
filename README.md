# Paper Desk

Seven paper traders, C$3,000 each, all books in Canadian dollars. **Paper money only: nothing here can reach a broker.**

| Trader | Style |
|---|---|
| Dax | Day trader: small caps, super aggressive |
| Sam | Swing: news, earnings, rates and yields, 10+ trades a week |
| Lena | Long-term: quarters, earnings, dividends, growth |
| Fox | Futures and commodities: gold, silver (sandbox, not possible in a TFSA) |
| Vega | Options, super aggressive |
| Theo | Options, safe (the only safe one) |
| Kai | Crypto: BTC/ETH core, news, memes, growth coins (sandbox) |

Dashboard: https://ganga27321.github.io/paper-desk/

- A 7 AM (Toronto) meeting writes the day's plan; a fast whole-market US + Canada scan follows.
- The engine replays 5-minute bars, so fills are honest even when GitHub's schedule runs late. TFSA traders follow
  TFSA rules (long only, no margin, regular hours, whole shares, long options / covered calls / cash-secured puts).
- Style scales (0-10) drive each trader's rules and can be moved from the dashboard.
- Saturday: weekly review, scale adjustments, and C$200 to the week's biggest earner. A weekly PDF follows.

State (accounts, trades, plans, scans) lives on the `paper-data` branch. `paper-desk.yml` runs everything.
Playbook: `papertrade/MEETING.md`. Not financial advice.
