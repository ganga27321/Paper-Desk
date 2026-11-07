"""Weekly summary PDF of all seven paper accounts (matplotlib only). Written to state/weekly/<week>.pdf."""
import csv
import datetime as dt
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from . import core

COL = {"day": "#2a78d6", "swing": "#eb6834", "long": "#1baf7a", "futures": "#eda100", "options_aggr": "#e87ba4",
       "options_safe": "#008300", "crypto": "#6250d6"}
INK, INK2, LINE = "#1d1d1f", "#6e6e73", "#e5e5ea"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": LINE, "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False})


def _equity():
    out = {}
    p = core.STATE / "equity.csv"
    if p.exists():
        with p.open(newline="") as f:
            for r in csv.DictReader(f):
                out.setdefault(r["trader"], []).append((dt.datetime.fromisoformat(r["ts"].rstrip("Z")), float(r["equity"])))
    return out


def _page(pdf, title, sub=""):
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.text(0.07, 0.95, title, fontsize=20, weight="bold", color=INK)
    if sub:
        fig.text(0.07, 0.925, sub, fontsize=10, color=INK2)
    return fig


def award(week_end=None):
    """After Friday's close: the biggest dollar earner of the week gets the weekly bonus (a deposit, not a gain).
    Week P/L = equity now - equity at the start of the week - deposits during the week. Paid once per week."""
    import json
    week_end = week_end or core.ny().date()
    week_start = week_end - dt.timedelta(days=week_end.weekday())
    tag = f"{week_end.isocalendar()[0]}-W{week_end.isocalendar()[1]:02d}"
    bp = core.STATE / "bonuses.json"
    paid = json.loads(bp.read_text()) if bp.exists() else {}
    if tag in paid:
        return paid[tag]
    held = sorted(core.CFG.get("bonus_hold") or [])
    if tag in held:   # the owner held this week's bonus back: no payout, the C$ carries to the next paid week
        core.log("bonus_held", week=tag)
        return None
    acc, eq = core.load_accounts(), _equity()
    board = []
    for tid, t in core.CFG["traders"].items():
        s = eq.get(tid, [])
        if not s:
            continue
        before = [v for ts, v in s if ts.date() < week_start]
        start = before[-1] if before else t["capital"]
        dep = sum(d["amount"] for d in acc[tid]["deposits"] if d["date"] >= week_start.isoformat())
        board.append((s[-1][1] - start - dep, tid))
    if not board:
        return None
    board.sort(reverse=True)
    pnl, win = board[0]
    base_amt = float(core.CFG.get("weekly_bonus", 200))
    carried = sum(1 for h in held if h < tag and h not in paid)   # held weeks roll into this one
    amt = base_amt * (1 + carried)
    a = acc[win]
    a["cash"] += amt
    a["deposits"].append({"date": week_end.isoformat(), "amount": amt, "why": f"Winner of {tag}"})
    name = core.CFG["traders"][win]["name"]
    core.append_trade({"ts": core.utcnow().isoformat() + "Z", "trader": win, "action": "BONUS", "kind": "cash", "symbol": "BONUS",
                       "qty": 1, "price": amt, "fee": 0, "value": amt, "pnl": "",
                       "why": f"Weekly bonus: biggest earner of {tag} ({pnl:+,.2f} C$ this week). C${amt:.0f} added to the account.",
                       "source": "desk", "tfsa_legal": "yes" if core.CFG["traders"][win]["tfsa"] else "no (sandbox)"})
    core.save_accounts(acc)
    paid[tag] = {"winner": win, "name": name, "week_pnl": round(pnl, 2), "bonus": amt,
                 "board": [{"trader": t, "week_pnl": round(v, 2)} for v, t in board]}
    bp.write_text(json.dumps(paid, indent=1))
    core.log("bonus", trader=win, amount=amt, week=tag, week_pnl=round(pnl, 2))
    return paid[tag]


def build(week_end=None):
    week_end = week_end or core.ny().date()
    week_start = week_end - dt.timedelta(days=week_end.weekday())
    tag = f"{week_end.isocalendar()[0]}-W{week_end.isocalendar()[1]:02d}"
    bonus = award(week_end)
    acc, trades, eq = core.load_accounts(), core.read_trades(), _equity()
    wk = [t for t in trades if t["ts"][:10] >= week_start.isoformat()]
    out = core.STATE / "weekly" / f"Paper-Desk-{tag}.pdf"
    out.parent.mkdir(parents=True, exist_ok=True)
    T = core.CFG["traders"]
    with PdfPages(out) as pdf:
        # page 1: the desk
        fig = _page(pdf, "Paper Desk - weekly summary", f"Week of {week_start:%b %d} to {week_end:%b %d, %Y} · paper money only")
        rows, total, start = [], 0, 0
        for tid, t in T.items():
            s = eq.get(tid, [])
            e_now = s[-1][1] if s else acc[tid]["cash"]
            prior = [v for ts, v in s if ts.date() < week_start]
            e_wk0 = prior[-1] if prior else t["capital"]
            n = len([x for x in wk if x["trader"] == tid])
            pnl = sum(float(x["pnl"]) for x in wk if x["trader"] == tid and x["pnl"])
            put_in = t["capital"] + sum(d["amount"] for d in acc[tid]["deposits"])
            rows.append([t["name"], t["title"], f"C${e_now:,.0f}", f"{e_now / put_in - 1:+.1%}", f"{e_now / e_wk0 - 1:+.1%}",
                         str(n), f"{pnl:+,.0f}", "TFSA" if t["tfsa"] else "Sandbox"])
            total += e_now
            start += put_in
        fig.text(0.07, 0.86, f"C${total:,.0f}", fontsize=34, weight="bold", color=INK)
        fig.text(0.07, 0.835, f"{total / start - 1:+.1%} on C${start:,.0f} put in · {len(wk)} trades this week", fontsize=11, color=INK2)
        if bonus:
            fig.text(0.07, 0.812, f"Winner of the week: {bonus['name']} ({bonus['week_pnl']:+,.2f} C$) - C${bonus['bonus']:.0f} bonus added",
                     fontsize=11, color="#067806", weight="bold")
        ax = fig.add_axes([0.07, 0.50, 0.86, 0.29])
        for tid, s in eq.items():
            if s:
                ax.plot([a for a, _ in s], [b for _, b in s], color=COL[tid], lw=1.8, label=T[tid]["name"])
        ax.axhline(3000, color=INK2, lw=0.8, ls=(0, (3, 4)))
        ax.set_title("Equity by trader (C$)", loc="left", color=INK, fontsize=11)
        ax.legend(ncol=4, frameon=False, fontsize=8, loc="upper left")
        ax.grid(axis="y", color=LINE)
        fig.autofmt_xdate()
        tab = fig.add_axes([0.07, 0.07, 0.86, 0.36]); tab.axis("off")
        tb = tab.table(cellText=rows, colLabels=["Trader", "Role", "Equity", "Since start", "This week", "Trades", "Week P/L C$", "Rules"],
                       loc="upper left", cellLoc="left", colLoc="left")
        tb.auto_set_font_size(False); tb.set_fontsize(8.5); tb.scale(1, 1.6)
        for (r, c), cell in tb.get_celld().items():
            cell.set_edgecolor(LINE)
            if r == 0:
                cell.set_text_props(weight="bold", color=INK2)
        pdf.savefig(fig); plt.close(fig)
        # one page per trader
        for tid, t in T.items():
            a = acc[tid]
            fig = _page(pdf, f"{t['name']} - {t['title']}", t["style"] + ("" if t["tfsa"] else "   (sandbox: not possible in a TFSA)"))
            s = eq.get(tid, [])
            ax = fig.add_axes([0.07, 0.66, 0.86, 0.22])
            if s:
                ax.plot([x for x, _ in s], [y for _, y in s], color=COL[tid], lw=2)
            ax.axhline(t["capital"], color=INK2, lw=0.8, ls=(0, (3, 4)))
            ax.grid(axis="y", color=LINE); ax.set_title("Equity (C$)", loc="left", color=INK, fontsize=11)
            fig.autofmt_xdate()
            y = 0.60
            fig.text(0.07, y, f"Cash C${a['cash']:,.2f} · open positions {len(a['positions'])} · realized to date C${a.get('realized', 0):+,.2f}", color=INK)
            y -= 0.03
            fig.text(0.07, y, "Open positions", weight="bold", color=INK); y -= 0.02
            for p in a["positions"][:8]:
                line = f"{p['symbol']} {p['qty']:g} @ {p['entry']:,.4g}  - {p.get('why', '')}"
                for w in textwrap.wrap(line, 110)[:2]:
                    fig.text(0.08, y, w, fontsize=8, color=INK2); y -= 0.016
            if not a["positions"]:
                fig.text(0.08, y, "None - all cash.", fontsize=8, color=INK2); y -= 0.016
            y -= 0.015
            fig.text(0.07, y, "This week's trades (what, why, how much)", weight="bold", color=INK); y -= 0.02
            mine = [x for x in wk if x["trader"] == tid]
            for x in mine[-18:]:
                head = f"{x['ts'][5:16].replace('T', ' ')} {x['action']} {x['qty']} {x['symbol']} @ {float(x['price']):,.4g} = C${float(x['value']):,.0f}" + \
                       (f" ({x['pct_of_equity']}% of equity)" if x["pct_of_equity"] else "") + (f"  P/L {float(x['pnl']):+,.2f}" if x["pnl"] else "")
                fig.text(0.08, y, head, fontsize=8, color=INK); y -= 0.015
                for w in textwrap.wrap(x["why"], 118)[:2]:
                    fig.text(0.10, y, w, fontsize=7.5, color=INK2); y -= 0.014
                if y < 0.05:
                    break
            if not mine:
                fig.text(0.08, y, "No trades this week.", fontsize=8, color=INK2)
            pdf.savefig(fig); plt.close(fig)
    return str(out)


def review(path=None):
    """Saturday council session: apply the week's review (insights + scale adjustments), pay the weekly bonus,
    build the PDF. Idempotent: a review is applied once, the bonus is paid once per week."""
    import json
    from . import settings
    ny = core.ny()
    tag = f"{ny.isocalendar()[0]}-W{ny.isocalendar()[1]:02d}"
    path = path or core.STATE / "reviews" / f"{tag}.json"
    applied_p = core.STATE / "reviews_applied.json"
    applied = json.loads(applied_p.read_text()) if applied_p.exists() else []
    if path and __import__("pathlib").Path(path).exists() and str(path) not in applied:
        doc = json.loads(open(path, encoding="utf-8").read())
        settings.apply_doc(doc.get("adjustments", {}), by=f"Saturday council {doc.get('week', tag)}")
        applied_p.write_text(json.dumps(applied + [str(path)]))
        core.log("review", week=doc.get("week", tag), traders=list((doc.get("weekly_review") or {}).keys()))
    return build(ny.date())
