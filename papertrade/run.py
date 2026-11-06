"""One Paper Desk cycle: fills and exits on new bars, new decisions, marking, summary for the dashboard."""
import csv
import datetime as dt
import json

from . import core, data, edge, engine, scanner, settings
from .strategies import RULEBOOK, plan_orders

CA = (".TO", ".V")
PRICE_KEYS = ("price", "atr", "hi20", "lo20", "sma20", "sma50", "sma200", "prev_close", "or_high", "or_low", "vwap")


def _cad_rows(rows, fx):
    """Scanner rows are in each listing's own currency; the desk keeps every account in C$."""
    out = []
    for r in rows:
        if not r.get("symbol", "").endswith(CA):
            r = {**r, **{k: r[k] * fx for k in PRICE_KEYS if isinstance(r.get(k), (int, float))}}
        out.append(r)
    return out


def _load_scan(name, fx):
    p = scanner.SCANS / name
    if not p.exists():
        return {}
    d = json.loads(p.read_text())
    if "lists" in d:
        d["lists"] = {k: _cad_rows(v, fx) for k, v in d["lists"].items()}
        d["tabled"] = {s: _cad_rows([{**m, "symbol": s}], fx)[0] for s, m in d.get("tabled", {}).items()}
    if "rows" in d:
        d["rows"] = _cad_rows([r for r in d["rows"] if not r["symbol"].endswith(CA)], fx)  # day trades: US small caps
    return d


def _report_rows(fx):
    try:
        ticks = json.loads(core.FEED.read_text()).get("plays", [])[:8]
    except Exception:
        return []
    d = data.daily(ticks, "1y")
    rows = []
    for s in ticks:
        m = scanner.metrics(d[s]) if s in d else None
        if m:
            rows.append({"symbol": s, **m})
    return _cad_rows(rows, fx)


def _tnx_5d():
    d = data.daily(["^TNX"], "1mo").get("^TNX")
    return float(d["close"].iloc[-1] - d["close"].iloc[-6]) if d is not None and len(d) > 6 else None


def _bars_cad(syms, fx, interval="5m", period="5d"):
    b = data.bars(syms, interval, period)
    for s in list(b):
        if not s.endswith(CA):
            b[s] = b[s].assign(**{c: b[s][c] * fx for c in ("open", "high", "low", "close")})
    return b


def _syms(a):
    return {x["meta"].get("yahoo", x["symbol"]) for x in a["positions"] + a["pending"] if x["kind"] != "option"}


def _levels(plan, fx):
    """Council levels from the morning plan, in C$: {symbol: {"stop", "target", "no_buy_above"}}."""
    out = {}
    for sym, lv in (plan or {}).get("levels", {}).items():
        k = 1 if sym.endswith(CA) else fx
        out[sym] = {f: float(lv[f]) * k for f in ("stop", "target", "no_buy_above") if lv.get(f) not in (None, "")}
    return out


def _limits(tid, plan):
    lim = dict(core.CFG.get("guards", {}).get("default", {}))
    lim.update(core.CFG.get("guards", {}).get(tid, {}))
    lim.update(((plan or {}).get("limits") or {}).get(tid, {}))
    return lim


def due(every_min=14):
    """Dax (day trader) runs every 5 minutes; the other six only when their last full run is ~15 minutes old."""
    p = core.STATE / "last_full_cycle.txt"
    now = core.utcnow()
    try:
        last = dt.datetime.fromisoformat(p.read_text().strip())
    except Exception:
        last = None
    if last is None or (now - last).total_seconds() >= every_min * 60:
        core.STATE.mkdir(parents=True, exist_ok=True)
        p.write_text(now.isoformat())
        return None
    return ["day"]


def cycle(only=None):
    now = core.utcnow()
    tny = core.ny(now)
    date = tny.date().isoformat()
    week = f"{tny.isocalendar()[0]}-W{tny.isocalendar()[1]:02d}"
    acc = core.load_accounts()
    fx = data.usdcad()
    plan = core.plan_for(date)
    applied = settings._load("plans_applied.json", [])
    if plan and plan.get("adjustments") and date not in applied:  # Thursday's weekly review moves the scales
        settings.apply_doc(plan["adjustments"], by=f"meeting {date}")
        settings._save("plans_applied.json", applied + [date])
    pre, intr = _load_scan("premarket.json", fx), _load_scan("intraday.json", fx)
    tids = [t for t in core.CFG["traders"] if not only or t in only]
    for tid in tids:
        a = acc[tid]
        if a["day"]["date"] != date:
            a["day"] = {"date": date, "trades": 0, "start_eq": a.get("equity_last") or core.CFG["traders"][tid]["capital"]}
        if a["week"]["key"] != week:
            a["week"] = {"key": week, "trades": 0}
    allsyms = set().union(*[_syms(acc[t]) for t in tids if t != "day"])
    bars = _bars_cad(sorted(allsyms), fx)
    day_bars = _bars_cad(sorted(_syms(acc["day"])), fx, "1m", "2d") if "day" in tids else {}  # Dax: 1-minute bars
    prices = {s: float(df["close"].iloc[-1]) for s, df in {**bars, **day_bars}.items()}
    ctx_base = {"date": date, "now_utc": now, "now_ny": tny, "plan": plan, "premarket": pre, "intraday": intr,
                "prices": prices, "tnx_5d": _tnx_5d(), "report_rows": _report_rows(fx),
                "tabled_rows": list(pre.get("tabled", {}).values()), "fx": fx, "regime": edge.regime()}
    equity_rows = []
    levels = _levels(plan, fx)
    n_trades0 = len(core.read_trades())
    alerts = []
    for tid in tids:
        a = acc[tid]
        _, eq = engine.mark(a, prices)
        a["deploy_frac"], a["equity_last"] = settings.rules(tid)["deploy_frac"], eq
        engine.replay(tid, a, day_bars if tid == "day" else bars, eq, now)
        if any(p["kind"] == "option" for p in a["positions"] + a["pending"]):
            engine.manage_options(tid, a, eq, tny.date())
        # expire stale orders, time stops
        a["pending"] = [o for o in a["pending"] if dt.datetime.fromisoformat(o["valid_until"]) > now or o["kind"] == "option"]
        for p in a["positions"]:
            if p.get("time_stop") and date >= p["time_stop"] and not p.get("exit_next"):
                p["exit_next"] = f"time stop: {p['time_stop']} reached without stop or target"
        pv, eq = engine.mark(a, prices)
        margin = sum(p.get("margin", 0) for p in a["positions"])
        if margin and eq < margin * settings.rules(tid).get("maint_pct", 0.9):
            for p in [p for p in a["positions"] if p["kind"] == "future"]:
                engine.close_position(tid, a, p, prices.get(p["meta"]["yahoo"], p["entry"]), "margin call: liquidated", eq)
            pv, eq = engine.mark(a, prices)
        ctx = {**ctx_base, "equity": eq}
        lim = _limits(tid, plan)
        for p in a["positions"]:  # the council's stop is a floor for every long on that symbol
            lv = levels.get(p["meta"].get("yahoo", p["symbol"])) or levels.get(p["symbol"])
            if lv and lv.get("stop") and p.get("side", "long") == "long" and p["kind"] != "option" and lv["stop"] > (p.get("stop") or 0):
                p["stop"] = lv["stop"]
        start_eq = a["day"].get("start_eq") or eq
        max_loss = lim.get("max_day_loss_pct")
        if max_loss and not a["day"].get("halted") and eq < start_eq * (1 - max_loss / 100):
            a["day"]["halted"] = f"day loss {eq / start_eq - 1:.1%} hit the {max_loss}% limit"
            a["pending"] = [o for o in a["pending"] if o["kind"] == "option" and o.get("action") != "BUY_TO_OPEN"]
            core.log("halt", trader=tid, reason=a["day"]["halted"])
            alerts.append(f"⛔ {core.CFG['traders'][tid]['name']}: stopped for the day ({a['day']['halted']})")
        proposals = []
        if a["day"].get("halted"):
            pass
        elif lim.get("max_trades_day") and a["day"]["trades"] >= lim["max_trades_day"]:
            pass
        else:
            try:
                proposals = plan_orders(tid, a, ctx) + RULEBOOK[tid](tid, a, ctx)
            except Exception as e:  # one trader's bad day never stops the others
                core.log("error", trader=tid, error=repr(e)[:300])
        for o in proposals:
            if "close" in o:
                for p in a["positions"]:
                    if p["id"] == o["close"]:
                        p["exit_next"] = o["why"]
                continue
            why_not = core.tfsa_check(tid, o)
            rules = settings.rules(tid)
            if o.get("play") and not o.get("why", "").startswith("["):
                o["why"] = f"[{o['play']}] " + o.get("why", "")
            if o["kind"] != "option" and edge.cooling(tid, o["symbol"], tny.date()):
                why_not = why_not or f"cooldown: stopped out of {o['symbol']} within {rules.get('cooldown_days')} days"
            if o["kind"] in ("stock", "crypto", "future") and rules.get("scale_out_r") and o.get("scale_out_r") is None:
                o["scale_out_r"] = rules["scale_out_r"]
            mult = edge.size_mult(tid, ctx_base["regime"])
            if mult < 1 and o.get("action") != "SELL_TO_OPEN":  # Theo's sold puts handle the mood in his own rules
                o["qty"] = core.whole(o["qty"] * mult) if o["kind"] in ("stock", "future", "option") else round(o["qty"] * mult, 8)
                o["why"] = o.get("why", "") + f" Sized x{mult:g} for a {ctx_base['regime']['mode'].replace('_', '-')} market."
                if o["qty"] <= 0:
                    why_not = why_not or "market mood sized it to zero"
            ysym = o["meta"].get("yahoo", o["symbol"])
            est = o.get("price") or prices.get(ysym) or ((data.last_price(ysym) or 0) * (1 if ysym.endswith(CA) else fx))
            lv = levels.get(ysym) or levels.get(o["symbol"])
            if lv and o["kind"] != "option" and o.get("side", "long") == "long":
                if lv.get("no_buy_above") and est and est > lv["no_buy_above"]:
                    why_not = why_not or "above the council's no-buy level"
                if lv.get("stop") and lv["stop"] > (o.get("stop") or 0):
                    o["stop"] = lv["stop"]
            if not why_not and o["kind"] in ("stock", "future", "crypto") and not o.get("stop"):
                why_not = "no stop loss"
            if not why_not and o["kind"] in ("stock", "crypto") and o.get("side", "long") == "long":
                bp = engine.buying_power(a)
                if o["qty"] * est > bp:
                    o["qty"] = (core.whole(bp / est) if o["kind"] == "stock" else round(bp / est, 8)) if est else 0
                if o["qty"] <= 0:
                    why_not = "not enough buying power"
                o["reserve"] = o["qty"] * est
            if why_not:
                core.log("rejected", trader=tid, symbol=o["symbol"], reason=why_not, why=o.get("why"))
                continue
            a["pending"].append(o)
            core.log("order", trader=tid, symbol=o["symbol"], kind=o["kind"], qty=o["qty"], type=o.get("entry_type"),
                     price=o.get("price"), why=o.get("why"), source=o.get("source"))
        # orders for symbols we had no bars for yet: fetch now so a market order can fill on the next run
        pv, eq = engine.mark(a, prices)
        a["equity_last"] = eq
        equity_rows.append((tid, a["cash"], pv, eq))
    core.append_equity(now.isoformat() + "Z", equity_rows)
    core.save_accounts(acc)
    live_update(core.read_trades()[n_trades0:], alerts, tny)
    return equity_rows


def live_update(new, alerts, tny):
    """One phone note per run with every fill, stop, target and halt (nothing sent when nothing happened)."""
    if not new and not alerts:
        return
    T = core.CFG["traders"]
    lines = list(alerts)
    for t in new:
        pnl = f" · P/L C${float(t['pnl']):+,.0f}" if t.get("pnl") not in ("", None) else ""
        lines.append(f"{T.get(t['trader'], {}).get('name', t['trader'])}: {t['action'].lower()} {float(t['qty']):g} {t['symbol']} "
                     f"@ {float(t['price']):,.4g}{pnl} - {t.get('why', '')[:90]}")
    from . import notify
    notify.send(f"Paper Desk {tny:%H:%M} · {len(new)} trade{'s' * (len(new) != 1)}", "\n".join(lines)[:3500],
                buttons=(("Dashboard", ""),), url=core.CFG.get("site_url", ""), tags=("robot",))


def scoreboard():
    """Closing note: each trader's day and week in C$."""
    acc = core.load_accounts()
    tny = core.ny()
    eq = {}
    p = core.STATE / "equity.csv"
    rows = list(csv.DictReader(p.open())) if p.exists() else []
    today = tny.date().isoformat()
    lines = []
    for tid, t in core.CFG["traders"].items():
        mine = [r for r in rows if r["trader"] == tid]
        if not mine:
            continue
        last = float(mine[-1]["equity"])
        start = acc[tid]["day"].get("start_eq") or last
        n = sum(1 for r in core.read_trades() if r["trader"] == tid and r["ts"][:10] == today)
        halted = " ⛔" if acc[tid]["day"].get("halted") else ""
        eq[tid] = last
        lines.append(f"{t['name']:<5} C${last:,.0f}  day {last - start:+,.0f}  trades {n}{halted}")
    lines.sort(key=lambda s: -float(s.split("day ")[1].split()[0].replace(",", "")))
    from . import notify
    notify.send(f"Paper Desk close {today}", "\n".join(lines) + "\n\nPaper money only.", buttons=(("Dashboard", ""),),
                url=core.CFG.get("site_url", ""), tags=("checkered_flag",))


def scan(kind):
    plan = core.plan_for(core.ny().date().isoformat()) or {}
    tabled = plan.get("tabled", [])
    return {"premarket": scanner.premarket, "deep": scanner.deep}.get(kind, scanner.intraday)(tabled)
