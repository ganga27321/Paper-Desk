"""The 7 AM meeting: context for the Claude session that convenes the seven traders, plan validation, and the
ntfy note that shares the plan."""
import datetime as dt
import glob
import json
import re
import sys

from . import core, data, edge, scanner, settings

T = core.CFG["traders"]


def _tail(path, n):
    try:
        return open(path, encoding="utf-8").read().splitlines()[-n:]
    except OSError:
        return []


def _week(tid, a, trades):
    ny = core.ny()
    start = (ny.date() - dt.timedelta(days=ny.weekday())).isoformat()
    mine = [r for r in trades if r["trader"] == tid and r["ts"][:10] >= start]
    pnl = [float(r["pnl"]) for r in mine if r["pnl"]]
    return {"trades": len(mine), "closed": len(pnl), "realized": round(sum(pnl), 2),
            "win_rate": round(sum(p > 0 for p in pnl) / len(pnl), 2) if pnl else None,
            "best": max(pnl) if pnl else None, "worst": min(pnl) if pnl else None,
            "deposits": a.get("deposits", [])}


def _lessons():
    from . import weekly
    return {tid: [m["lesson"] for m in items] for tid, items in weekly.lessons().items()}


def context():
    """Everything the meeting needs, as one JSON document (printed)."""
    acc = core.load_accounts()
    snap = {}
    d = data.daily(["SPY", "QQQ", "IWM", "^TNX", "GC=F", "SI=F", "CL=F", "BTC-USD", "ETH-USD", "XIU.TO", "^VIX"], "1mo")
    for s, df in d.items():
        c = df["close"]
        snap[s] = {"last": round(float(c.iloc[-1]), 4), "d1": round(float(c.iloc[-1] / c.iloc[-2] - 1), 4),
                   "d5": round(float(c.iloc[-1] / c.iloc[-6] - 1), 4)}
    trades = core.read_trades()
    traders = {}
    for tid, t in T.items():
        a = acc[tid]
        eq_rows = [r for r in _tail(core.STATE / "equity.csv", 400) if f",{tid}," in r]
        traders[tid] = {"name": t["name"], "title": t["title"], "style": t["style"], "tfsa_rules": t["tfsa"],
                        "capital": t["capital"], "cash": round(a["cash"], 2), "equity_last": eq_rows[-1].split(",")[-1] if eq_rows else None,
                        "positions": [{k: p.get(k) for k in ("symbol", "kind", "side", "qty", "entry", "stop", "target", "why", "last", "mark")}
                                      for p in a["positions"]],
                        "pending": [{k: o.get(k) for k in ("symbol", "kind", "qty", "entry_type", "price", "why")} for o in a["pending"]],
                        "recent_trades": [r for r in trades if r["trader"] == tid][-8:],
                        "rules_now": settings.rules(tid), "scales": settings.current(tid),
                        "week": _week(tid, a, trades)}
    pm = scanner.SCANS / "premarket.json"
    pre = json.loads(pm.read_text()) if pm.exists() else {}
    yday = sorted(glob.glob(str(core.STATE / "plans" / "*.json")))
    out = {"now_ny": core.ny().isoformat(timespec="minutes"), "market": snap, "traders": traders,
           "crypto_fear_greed": edge.fear_greed_info(),   # source "saved" = the last desk run's reading (time in "at")
           "clock": clock(),
           "scanner_lists_previous": {k: [x["symbol"] for x in v[:15]] for k, v in pre.get("lists", {}).items()},
           "previous_plan": json.loads(open(yday[-1], encoding="utf-8").read()) if yday else None,
           "recent_log": _tail(core.STATE / "log.jsonl", 40),
           "weekday": core.ny().strftime("%A"),
           "bonuses": json.loads((core.STATE / "bonuses.json").read_text()) if (core.STATE / "bonuses.json").exists() else {},
           "scale_changes_recent": settings.history()[-30:],
           "lessons": _lessons()}
    return json.dumps(out, indent=1, default=str)


def _engine_pick(o):
    """The contract the engine would pick for a not-yet-placed option order right now, with its live quote."""
    sym, right = o["symbol"], o.get("right", "C")
    px = data.last_price(sym)
    exp = data.pick_expiry(sym, *(o.get("dte") or [7, 30]), core.ny().date())
    if not px or not exp:
        return None
    otm = o.get("otm_pct", 0.03)
    c = data.pick_contract(sym, exp, right, px * (1 + otm) if right == "C" else px * (1 - otm))
    if not c:
        return None
    prem = c["bid"] if o.get("action") == "SELL_TO_OPEN" else (c["ask"] or c["last"])
    mid = (c["bid"] + c["ask"]) / 2 if c["bid"] and c["ask"] else None
    return {"underlying_now": round(px, 2), "expiry": exp, "strike": c["strike"], "bid": c["bid"], "ask": c["ask"],
            "last": c["last"], "iv": round(c["iv"], 3) if c.get("iv") else None,
            "spread_pct": round(100 * (c["ask"] - c["bid"]) / mid, 1) if mid else None,
            "per_contract_cad": round(prem * 100 * data.usdcad(), 2) if prem else None}


def options_view():
    """For the 9:31 ET options convene: Vega's and Theo's books on live quotes, today's plan option orders (placed or
    not, and what the engine would pick now) and their Plan B/C blocks. Printed as one JSON document."""
    from .engine import buying_power
    acc = core.load_accounts()
    t = core.ny()
    date = t.date().isoformat()
    plan = core.plan_for(date) or {}
    fx = data.usdcad()
    out = {"clock": clock(), "options_go_live_et": core.CFG.get("options_from", "09:45"), "usdcad": fx, "traders": {}}
    for tid in ("options_aggr", "options_safe"):
        a = acc[tid]
        used = a.get("plan_used", {}).get(date, [])
        positions = []
        for p in a["positions"]:
            if p["kind"] != "option":
                positions.append({"symbol": p["symbol"], "qty": p["qty"], "entry_cad": round(p["entry"], 2)})
                continue
            m = p["meta"]
            q = data.contract_quote(m["contract"], m["underlying"], m["expiry"], m["right"], m["strike"]) or {}
            mid = (q["bid"] + q["ask"]) / 2 if q.get("bid") and q.get("ask") else q.get("last")
            mark = mid * m.get("fx", fx) if mid else None
            positions.append({"contract": p["symbol"], "side": p["side"], "qty": p["qty"], "entry_cad": round(p["entry"], 2),
                              "quote_usd": {k: q.get(k) for k in ("bid", "ask", "last")}, "mark_cad": round(mark, 2) if mark else None,
                              "pnl_pct": round(100 * (mark / p["entry"] - 1), 1) if mark and p["entry"] else None,
                              "days_to_expiry": (dt.date.fromisoformat(m["expiry"]) - t.date()).days})
        mine = (plan.get("traders") or {}).get(tid) or {}
        orders = []
        for i, o in enumerate(mine.get("orders") or []):
            row = {"index": i, "placed": i in used, "cancelled": bool(o.get("cancel")), "order": o}
            if o.get("kind") == "option" and not row["placed"] and not row["cancelled"]:
                row["engine_pick_now"] = _engine_pick(o)
            orders.append(row)
        r = settings.rules(tid)
        out["traders"][tid] = {
            "name": T[tid]["name"], "cash": round(a["cash"], 2), "buying_power": round(buying_power(a), 2),
            "rules": {k: r.get(k) for k in ("risk_pct", "dte_min", "dte_max", "otm_pct", "put_otm_pct", "take_profit_pct",
                                             "stop_pct", "max_positions", "max_strike", "exit_days_before_expiry")},
            "positions": positions, "pending": [{k: o.get(k) for k in ("symbol", "qty", "action", "created")} for o in a["pending"]],
            "plan_orders": orders,
            "plan_b": [{"index": j, "fired": f"plan_b{j}" in used, **b} for j, b in enumerate(mine.get("plan_b") or [])],
            "plan_c": [{"index": j, "fired": f"plan_c{j}" in used, **b} for j, b in enumerate(mine.get("plan_c") or [])],
            "lessons": _lessons().get(tid, [])}
    return json.dumps(out, indent=1, default=str)


def clock(now_utc=None):
    """What time it is for the market, so the meeting never treats a released number as still to come."""
    now_utc = now_utc or core.utcnow()
    t = core.ny(now_utc)
    s = core.session(t.date())
    if not s:
        status, note = "closed", "No stock session today (weekend or holiday): only crypto and futures trade."
    else:
        o = t.replace(hour=s[0].hour, minute=s[0].minute, second=0)
        c = t.replace(hour=s[1].hour, minute=s[1].minute, second=0)
        if t < o:
            status, note = "premarket", f"Market opens in {int((o - t).total_seconds() // 60)} min (9:30 ET)."
        elif t < c:
            status, note = "open", f"Market is OPEN; closes in {int((c - t).total_seconds() // 60)} min."
        else:
            status, note = "after_close", "Today's session is over; plan for after-hours crypto/futures and tomorrow."
    return {"now_et": t.strftime("%a %Y-%m-%d %H:%M ET"), "now_toronto": t.strftime("%H:%M (Toronto = ET)"), "status": status,
            "note": note,
            "rule": "Any scheduled event whose time is before now_et HAS HAPPENED: search for its actual result "
                    "(the number, the surprise vs forecast, the market's reaction so far) and plan on that. Only events "
                    "after now_et may be described as still to come."}


DETAILED_FROM = "2026-11-09"   # plans from this date on need scenarios, a playbook, Plan B/C and a failsafe per trader
WHEN_TYPES = {"time_after": ["time_et"], "price_below": ["symbol", "level"], "price_above": ["symbol", "level"],
              "vix_above": ["level"], "vix_below": ["level"], "unfilled_by": ["symbol", "time_et"],
              "stopped_out": ["symbol"], "day_loss_over": ["pct"]}


def _check_orders(errs, tid, orders, label):
    for i, o in enumerate(orders or []):
        if "symbol" not in o or "why" not in o:
            errs.append(f"{tid} {label} {i}: needs symbol and why")
        fake = {"kind": o.get("kind", "stock"), "side": o.get("side", "long"), "action": o.get("action"),
                "right": o.get("right"), "qty": o.get("qty", 1), "covered": o.get("covered"), "cash_secured": o.get("cash_secured")}
        if fake["kind"] in ("stock", "future", "crypto") and o.get("stop") in (None, ""):
            errs.append(f"{tid} {label} {i} ({o.get('symbol')}): needs a stop loss")
        r = core.tfsa_check(tid, fake)
        if r:
            errs.append(f"{tid} {label} {i} ({o.get('symbol')}): {r}")


def _hm(text):
    """'08:30', '8:30' or '8:30am' -> time; None if unreadable."""
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([ap]m)?", str(text or ""), re.I)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3) and m.group(3).lower() == "pm" and h < 12:
        h += 12
    return dt.time(h, mi) if h < 24 and mi < 60 else None


def validate(path, check_clock=True):
    """Check a plan file; prints problems and returns True if usable. check_clock=False skips the 'released events
    need results' test, for re-checks later in the day (the plan was right when it was written)."""
    p = json.loads(open(path, encoding="utf-8").read())
    errs = []
    review_file = "week" in p and "traders" not in p   # a Saturday review file
    if p.get("adjustments") and not review_file:
        errs.append("adjustments: scale changes happen only in the Saturday review (reviews/<week>.json), not in a daily plan")
    for tid, ch in (p.get("adjustments") or {}).items():
        if tid not in T:
            errs.append(f"adjustments: unknown trader {tid}")
            continue
        live = set(T[tid].get("scale_map") or {}) | {"deployment"}   # deployment works for every trader
        for k, v in (ch.get("scales") or {}).items():
            if k not in [x[0] for x in settings.KNOBS] or not 0 <= float(v) <= 10:
                errs.append(f"adjustments {tid}: bad scale {k}={v}")
            elif k not in live:
                errs.append(f"adjustments {tid}: the {k} scale drives nothing for {T[tid]['name']} "
                            f"(scales that do: {', '.join(sorted(live))})")
        if tid == "options_safe" and float((ch.get("scales") or {}).get("aggression", 0)) > 4:
            errs.append("adjustments options_safe: Theo is the safe player (aggression 4 or below)")
    if review_file:
        for k in ("week", "weekly_review", "lessons"):
            if k not in p:
                errs.append(f"review: missing {k}")
        for e in errs:
            print("REVIEW:", e)
        return not errs
    for k in ("date", "market_view", "tabled", "traders", "events"):
        if k not in p:
            errs.append(f"missing {k}" + (" (list today's events with time_et, status released/upcoming and the actual result if released)" if k == "events" else ""))
    now = core.ny()
    for e in p.get("events") or []:
        at = _hm(e.get("time_et"))
        if e.get("time_et") and at is None:
            errs.append(f"event '{e.get('name')}': time_et {e.get('time_et')!r} must look like 08:30")
        if check_clock and at and at <= now.time() and p.get("date") == now.date().isoformat():
            if e.get("status") != "released" or not e.get("actual"):
                errs.append(f"event '{e.get('name')}' at {e['time_et']} ET is already out (now {now:%H:%M} ET): add status released and the actual result")
    detailed = p.get("date", "") >= DETAILED_FROM
    if detailed:
        sc = p.get("scenarios") or []
        if len([x for x in sc if isinstance(x, dict) and x.get("if") and x.get("then")]) < 3:
            errs.append("scenarios: need at least 3 market if/then situations ({'if': news/level, 'then': what each trader does})")
    for tid, tp in (p.get("traders") or {}).items():
        if tid not in T:
            errs.append(f"unknown trader {tid}")
            continue
        _check_orders(errs, tid, tp.get("orders", []), "order")
        if not detailed:
            continue
        for k in ("view", "failsafe"):
            if not str(tp.get(k) or "").strip():
                errs.append(f"{tid}: missing {k}")
        if _lessons().get(tid) and not isinstance(tp.get("lessons_applied"), list):
            errs.append(f"{tid}: has lessons from past reviews: add lessons_applied (list of the lessons used)")
        pb = [x for x in tp.get("playbook") or [] if isinstance(x, dict) and x.get("if") and x.get("then")]
        if len(pb) < 3:
            errs.append(f"{tid}: playbook needs at least 3 if/then situations from today's news and levels")
        for name in ("plan_b", "plan_c"):
            blocks = tp.get(name)
            if not blocks:
                errs.append(f"{tid}: {name} is required (a block with when, note and orders; orders may be empty for 'stand aside')")
                continue
            for j, blk in enumerate(blocks):
                w = blk.get("when") or {}
                if w.get("type") not in WHEN_TYPES:
                    errs.append(f"{tid} {name}[{j}]: when.type must be one of {sorted(WHEN_TYPES)}")
                elif any(k not in w for k in WHEN_TYPES[w["type"]]):
                    errs.append(f"{tid} {name}[{j}]: when {w['type']} needs {WHEN_TYPES[w['type']]}")
                if not blk.get("note"):
                    errs.append(f"{tid} {name}[{j}]: needs a note saying why this plan exists")
                _check_orders(errs, tid, blk.get("orders", []), f"{name}[{j}] order")
    for sym, lv in (p.get("levels") or {}).items():
        if not isinstance(lv, dict) or not lv.get("stop"):
            errs.append(f"levels {sym}: needs at least a stop")
    for tid, lim in (p.get("limits") or {}).items():
        if tid not in T or not isinstance(lim, dict):
            errs.append(f"limits: bad entry {tid}")
    for e in errs:
        print("PLAN:", e)
    return not errs


def share(path):
    """Send the plan to ntfy (the morning note)."""
    from . import notify
    p = json.loads(open(path, encoding="utf-8").read())
    marker = core.STATE / "plans_shared.json"
    shared = json.loads(marker.read_text()) if marker.exists() else []
    if p.get("date") in shared or notify.sent_today("Paper Desk 7 AM plan", str(p.get("date"))):
        print("plan already shared today")
        return
    lines = [p.get("market_view", ""), "", "Tabled: " + ", ".join(p.get("tabled", [])[:20])]
    for tid, tp in p.get("traders", {}).items():
        lines.append("")
        if tid not in T:
            continue
        lines.append(f"{T[tid]['name']} ({T[tid]['title']}): {tp.get('view', '')}")
        for o in tp.get("orders", [])[:3]:
            lines.append(f"  - {o.get('symbol')}: {o.get('why', '')[:140]}")
    url = core.CFG.get("site_url", "")
    if notify.send(f"Paper Desk 7 AM plan {p.get('date')}", notify.fit(lines[:1], [("PLAN", lines[1:])], footer="Paper money only."),
                   buttons=(("Dashboard", ""),), url=url):
        marker.write_text(json.dumps((shared + [p.get("date")])[-30:]))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "validate":
        sys.exit(0 if validate(sys.argv[2], check_clock="--no-clock" not in sys.argv) else 1)
    if cmd == "share":
        share(sys.argv[2])
