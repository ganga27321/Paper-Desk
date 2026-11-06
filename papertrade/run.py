"""One Paper Desk cycle: fills and exits on new bars, new decisions, marking, summary for the dashboard."""
import datetime as dt
import json

from . import core, data, engine, scanner, settings
from .strategies import RULEBOOK, plan_orders

CA = (".TO", ".V")
PRICE_KEYS = ("price", "atr", "hi20", "lo20", "sma20", "sma50", "sma200", "prev_close", "or_high", "or_low")


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


def _bars_cad(syms, fx):
    b = data.bars(syms, "5m", "5d")
    for s in list(b):
        if not s.endswith(CA):
            b[s] = b[s].assign(**{c: b[s][c] * fx for c in ("open", "high", "low", "close")})
    return b


def _syms(a):
    return {x["meta"].get("yahoo", x["symbol"]) for x in a["positions"] + a["pending"] if x["kind"] != "option"}


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
            a["day"] = {"date": date, "trades": 0}
        if a["week"]["key"] != week:
            a["week"] = {"key": week, "trades": 0}
    allsyms = set().union(*[_syms(acc[t]) for t in tids])
    bars = _bars_cad(sorted(allsyms), fx)
    prices = {s: float(df["close"].iloc[-1]) for s, df in bars.items()}
    ctx_base = {"date": date, "now_utc": now, "now_ny": tny, "plan": plan, "premarket": pre, "intraday": intr,
                "prices": prices, "tnx_5d": _tnx_5d(), "report_rows": _report_rows(fx),
                "tabled_rows": list(pre.get("tabled", {}).values()), "fx": fx}
    equity_rows = []
    for tid in tids:
        a = acc[tid]
        _, eq = engine.mark(a, prices)
        a["deploy_frac"], a["equity_last"] = settings.rules(tid)["deploy_frac"], eq
        engine.replay(tid, a, bars, eq, now)
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
        proposals = []
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
            ysym = o["meta"].get("yahoo", o["symbol"])
            est = o.get("price") or prices.get(ysym) or ((data.last_price(ysym) or 0) * (1 if ysym.endswith(CA) else fx))
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
    return equity_rows


def scan(kind):
    plan = core.plan_for(core.ny().date().isoformat()) or {}
    tabled = plan.get("tabled", [])
    return {"premarket": scanner.premarket, "deep": scanner.deep}.get(kind, scanner.intraday)(tabled)
