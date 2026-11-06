"""The 7 AM meeting: context for the Claude session that convenes the seven traders, plan validation, and the
ntfy note that shares the plan."""
import datetime as dt
import glob
import json
import sys

from . import core, data, scanner, settings

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
           "clock": clock(),
           "scanner_lists_previous": {k: [x["symbol"] for x in v[:15]] for k, v in pre.get("lists", {}).items()},
           "previous_plan": json.loads(open(yday[-1], encoding="utf-8").read()) if yday else None,
           "recent_log": _tail(core.STATE / "log.jsonl", 40),
           "weekday": core.ny().strftime("%A"),
           "bonuses": json.loads((core.STATE / "bonuses.json").read_text()) if (core.STATE / "bonuses.json").exists() else {},
           "scale_changes_recent": settings.history()[-30:]}
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


def validate(path):
    """Check a plan file; prints problems and returns True if usable."""
    p = json.loads(open(path, encoding="utf-8").read())
    errs = []
    for tid, ch in (p.get("adjustments") or {}).items():
        if tid not in T:
            errs.append(f"adjustments: unknown trader {tid}")
        for k, v in (ch.get("scales") or {}).items():
            if k not in [x[0] for x in settings.KNOBS] or not 0 <= float(v) <= 10:
                errs.append(f"adjustments {tid}: bad scale {k}={v}")
        if tid == "options_safe" and float((ch.get("scales") or {}).get("aggression", 0)) > 4:
            errs.append("adjustments options_safe: Theo is the safe player (aggression 4 or below)")
    if "week" in p and "traders" not in p:   # a Saturday review file
        for k in ("week", "weekly_review"):
            if k not in p:
                errs.append(f"review: missing {k}")
        for e in errs:
            print("REVIEW:", e)
        return not errs
    for k in ("date", "market_view", "tabled", "traders", "events"):
        if k not in p:
            errs.append(f"missing {k}" + (" (list today's events with time_et, status released/upcoming and the actual result if released)" if k == "events" else ""))
    now_et = core.ny().strftime("%H:%M")
    for e in p.get("events") or []:
        if e.get("time_et") and e["time_et"] <= now_et and p.get("date") == core.ny().date().isoformat():
            if e.get("status") != "released" or not e.get("actual"):
                errs.append(f"event '{e.get('name')}' at {e['time_et']} ET is already out (now {now_et} ET): add status released and the actual result")
    for tid, tp in (p.get("traders") or {}).items():
        if tid not in T:
            errs.append(f"unknown trader {tid}")
            continue
        for i, o in enumerate(tp.get("orders", [])):
            if "symbol" not in o or "why" not in o:
                errs.append(f"{tid} order {i}: needs symbol and why")
            fake = {"kind": o.get("kind", "stock"), "side": o.get("side", "long"), "action": o.get("action"),
                    "right": o.get("right"), "qty": o.get("qty", 1), "covered": o.get("covered"), "cash_secured": o.get("cash_secured")}
            if fake["kind"] in ("stock", "future", "crypto") and o.get("stop") in (None, ""):
                errs.append(f"{tid} order {i} ({o.get('symbol')}): needs a stop loss")
            r = core.tfsa_check(tid, fake)
            if r:
                errs.append(f"{tid} order {i} ({o.get('symbol')}): {r}")
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
    if notify.sent_today("Paper Desk 7 AM plan", str(p.get("date"))):
        print("plan already shared today")
        return
    lines = [p.get("market_view", ""), "", "Tabled: " + ", ".join(p.get("tabled", [])[:20])]
    for tid, tp in p.get("traders", {}).items():
        lines.append("")
        lines.append(f"{T[tid]['name']} ({T[tid]['title']}): {tp.get('view', '')}")
        for o in tp.get("orders", [])[:3]:
            lines.append(f"  - {o.get('symbol')}: {o.get('why', '')[:140]}")
    url = core.CFG.get("site_url", "")
    notify.send(f"Paper Desk 7 AM plan {p.get('date')}", notify.fit(lines[:1], [("PLAN", lines[1:])], footer="Paper money only."),
                buttons=(("Dashboard", ""),), url=url)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "validate":
        sys.exit(0 if validate(sys.argv[2]) else 1)
    if cmd == "share":
        share(sys.argv[2])
