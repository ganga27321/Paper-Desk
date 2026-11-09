"""The Paper Desk engine: replays bars since the last run, fills paper orders, manages exits, marks to market.

Fills are decided bar by bar on the bars' own times, so a GitHub run that starts late still fills at the prices
that were there (it replays the bars it missed). Only new decisions are late, never the fills.
Stop and target in the same bar: the stop is assumed to hit first (the cautious answer).
"""
import datetime as dt

import pandas as pd

from . import core, data, settings

MULT_OPTION = 100


def _bar_end(ts, width=dt.timedelta(minutes=5)):
    return ts + width


def _width(df):
    """Bar width from the data itself (1-minute bars for Dax, 5-minute for the others)."""
    if len(df) > 1:
        w = (df.index[1:] - df.index[:-1]).min()
        if pd.notna(w) and w > dt.timedelta(0):
            return min(w.to_pytimedelta(), dt.timedelta(minutes=5))
    return dt.timedelta(minutes=5)


def _in_session(trader_id, bar_start_utc):
    if not core.CFG["traders"][trader_id]["tfsa"]:
        return True
    return core.market_open_at(core.ny(bar_start_utc))


def buying_power(a):
    reserved = sum(p.get("reserved", 0) for p in a["positions"])
    margin = sum(p.get("margin", 0) for p in a["positions"])
    pend = sum(o.get("reserve", 0) for o in a["pending"])
    keep = (1 - a.get("deploy_frac", 1.0)) * a.get("equity_last", a["cash"])
    return a["cash"] - reserved - margin - pend - keep


# ------------------------------------------------------------------ open / close
def _record(tid, action, kind, symbol, qty, price, fee_, pnl, why, source, equity, value=None):
    tfsa = core.CFG["traders"][tid]["tfsa"]
    value = value if value is not None else qty * price
    core.append_trade({"ts": core.utcnow().isoformat() + "Z", "trader": tid, "action": action, "kind": kind,
                       "symbol": symbol, "qty": round(qty, 8), "price": round(price, 6), "fee": round(fee_, 2),
                       "value": round(value, 2), "pnl": "" if pnl is None else round(pnl, 2),
                       "pct_of_equity": round(100 * abs(value) / equity, 1) if equity else "",
                       "why": why, "source": source, "tfsa_legal": "yes" if tfsa else "no (sandbox)"})


def open_position(tid, a, o, price, when, equity):
    kind, qty = o["kind"], o["qty"]
    small = o.get("meta", {}).get("small_cap", False)
    f = core.fee(kind, qty, price, small)
    pos = {"id": o["id"], "kind": kind, "symbol": o["symbol"], "side": o.get("side", "long"), "qty": qty,
           "entry": price, "entry_ts": when.isoformat(), "stop": o.get("stop"), "target": o.get("target"),
           "flat_by": o.get("flat_by"), "time_stop": o.get("time_stop"), "why": o.get("why", ""),
           "source": o.get("source", "autopilot"), "meta": o.get("meta", {}), "high": price, "low": price}
    for k in ("scale_out_r", "trail_pct", "play"):
        if o.get(k) is not None:
            pos[k] = o[k]
    if kind in ("stock", "crypto"):
        a["cash"] -= qty * price + f
        action = "BUY"
    elif kind == "future":
        spec = o["meta"]
        pos["margin"] = abs(qty) * price * spec["multiplier"] * settings.rules(tid)["margin_pct"]
        a["cash"] -= f
        action = "LONG" if pos["side"] == "long" else "SHORT"
    elif kind == "option":
        m = o["meta"]
        if o.get("action") == "SELL_TO_OPEN":
            a["cash"] += qty * MULT_OPTION * price - f
            if m["right"] == "P":
                pos["reserved"] = qty * MULT_OPTION * m["strike"] * m.get("fx", 1.0)
            pos["side"] = "short"
            action = "SELL_TO_OPEN"
        else:
            a["cash"] -= qty * MULT_OPTION * price + f
            action = "BUY_TO_OPEN"
    a["positions"].append(pos)
    a["day"]["trades"] += 1
    a["week"]["trades"] += 1
    value = qty * price * (MULT_OPTION if kind == "option" else o.get("meta", {}).get("multiplier", 1))
    _record(tid, action, kind, o["symbol"], qty, price, f, None, o.get("why", ""), o.get("source", "autopilot"), equity, value)
    core.log("open", trader=tid, symbol=o["symbol"], kind=kind, qty=qty, price=price, at=when.isoformat())


def close_position(tid, a, pos, price, why, equity):
    kind, qty = pos["kind"], pos["qty"]
    f = core.fee(kind, qty, price, pos.get("meta", {}).get("small_cap", False))
    if kind in ("stock", "crypto"):
        a["cash"] += qty * price - f
        pnl = qty * (price - pos["entry"]) - f
        action, value = "SELL", qty * price
    elif kind == "future":
        mult = pos["meta"]["multiplier"]
        sign = 1 if pos["side"] == "long" else -1
        pnl = sign * qty * (price - pos["entry"]) * mult - f
        a["cash"] += pnl
        action, value = ("SELL" if sign > 0 else "COVER"), qty * price * mult
    else:  # option
        if pos["side"] == "short":
            a["cash"] -= qty * MULT_OPTION * price + f
            pnl = qty * MULT_OPTION * (pos["entry"] - price) - f
            action = "BUY_TO_CLOSE"
        else:
            a["cash"] += qty * MULT_OPTION * price - f
            pnl = qty * MULT_OPTION * (price - pos["entry"]) - f
            action = "SELL_TO_CLOSE"
        value = qty * MULT_OPTION * price
    a["realized"] = a.get("realized", 0) + pnl
    a["positions"] = [p for p in a["positions"] if p["id"] != pos["id"]]
    _record(tid, action, kind, pos["symbol"], qty, price, f, pnl, why, pos.get("source", "autopilot"), equity, value)
    core.log("close", trader=tid, symbol=pos["symbol"], kind=kind, price=price, pnl=round(pnl, 2), why=why)
    return pnl


def partial_close(tid, a, pos, qty, price, why, equity):
    """Close part of a position (scale-outs). The rest keeps its id, entry and rules."""
    qty = min(qty, pos["qty"])
    if qty <= 0:
        return 0
    if qty >= pos["qty"]:
        return close_position(tid, a, pos, price, why, equity)
    part = {**pos, "id": pos["id"] + "p", "qty": qty}
    for k in ("margin", "reserved"):
        if pos.get(k):
            part[k] = pos[k] * qty / pos["qty"]
            pos[k] = pos[k] - part[k]
    pos["qty"] = pos["qty"] - qty
    a["positions"].append(part)
    return close_position(tid, a, part, price, why, equity)


def _scale_out(tid, a, p, b, equity):
    """Sell half at `scale_out_r` x the initial risk, then the rest rides with its stop at breakeven."""
    k = p.get("scale_out_r")
    if not k or p.get("scaled") or p.get("stop") is None:
        return
    long = p.get("side", "long") == "long"
    init = p.get("init_stop", p["stop"])
    r = (p["entry"] - init) if long else (init - p["entry"])
    if r <= 0:
        return
    lvl = p["entry"] + k * r if long else p["entry"] - k * r
    if (long and b["high"] >= lvl) or (not long and b["low"] <= lvl):
        half = core.whole(p["qty"] / 2) if p["kind"] in ("stock", "future") else round(p["qty"] / 2, 8)
        p["scaled"] = True
        p.setdefault("init_stop", init)
        if half > 0:
            partial_close(tid, a, p, half, float(max(b["open"], lvl) if long else min(b["open"], lvl)),
                          f"took half at {k:g}R; rest rides with the stop at breakeven", equity)
            p["stop"] = max(p["stop"], p["entry"]) if long else min(p["stop"], p["entry"])


# ------------------------------------------------------------------ bar replay
def _entry_fill(o, b):
    t = o.get("entry_type", "market")
    long = o.get("side", "long") == "long"
    if t == "market":
        return b["open"]
    if t == "limit":
        if long and b["low"] <= o["price"]:
            return min(b["open"], o["price"])
        if not long and b["high"] >= o["price"]:
            return max(b["open"], o["price"])
    if t == "stop":  # breakout: buy when price trades through the trigger
        if long and b["high"] >= o["price"]:
            return max(b["open"], o["price"])
        if not long and b["low"] <= o["price"]:
            return min(b["open"], o["price"])
    return None


def _exit_fill(p, b, t_ny):
    long = p.get("side", "long") == "long"
    if p.get("exit_next"):
        return b["open"], p["exit_next"]
    st, tg = p.get("stop"), p.get("target")
    if st is not None:
        if long and b["low"] <= st:
            return min(b["open"], st), "stop hit"
        if not long and b["high"] >= st:
            return max(b["open"], st), "stop hit"
    if tg is not None:
        if long and b["high"] >= tg:
            return max(b["open"], tg), "target hit"
        if not long and b["low"] <= tg:
            return min(b["open"], tg), "target hit"
    if p.get("flat_by") and t_ny.time() >= core.hhmm(p["flat_by"]):
        return b["open"], f"flat by {p['flat_by']} (day trade closed before the bell)"
    return None


def _trail(p, b):
    """Move the stop up as price rises: aggressive traders lock in gains after 1R."""
    if p.get("stop") is None or p.get("side", "long") != "long":
        return
    p["high"] = max(p.get("high", p["entry"]), b["high"])
    r = p["entry"] - p.get("init_stop", p["stop"])
    p.setdefault("init_stop", p["stop"])
    if p.get("trail_pct"):  # percentage trail (Kai's news and meme coins)
        p["stop"] = max(p["stop"], round(p["high"] * (1 - p["trail_pct"] / 100), 8))
    if r > 0 and p["high"] >= p["entry"] + r:
        p["stop"] = max(p["stop"], round(p["entry"], 4))           # breakeven after 1R
    if r > 0 and p["high"] >= p["entry"] + 2 * r and p.get("target") is None:
        p["stop"] = max(p["stop"], round(p["high"] - r, 4))         # trail by 1R after 2R


def replay(tid, a, bars_by_sym, equity, now):
    """Fill pending orders and run exits on every bar this trader has not seen yet."""
    for sym in sorted({o["meta"].get("yahoo", o["symbol"]) for o in a["pending"] if o["kind"] != "option"} |
                      {p["meta"].get("yahoo", p["symbol"]) for p in a["positions"] if p["kind"] != "option"}):
        df = bars_by_sym.get(sym)
        if df is None:
            continue
        last = a["last_bar"].get(sym)
        last = dt.datetime.fromisoformat(last) if last else None
        width = _width(df)
        for ts, b in df.iterrows():
            ts = ts.to_pydatetime()
            if (last and ts <= last) or _bar_end(ts, width) > now:
                continue  # already seen, or still forming
            a["last_bar"][sym] = ts.isoformat()
            if not _in_session(tid, ts):
                continue
            t_ny = core.ny(ts)
            for p in [p for p in a["positions"] if p["meta"].get("yahoo", p["symbol"]) == sym and p["kind"] != "option"]:
                if dt.datetime.fromisoformat(p["entry_ts"]) >= ts:
                    continue
                hit = _exit_fill(p, b, t_ny)
                if hit:
                    close_position(tid, a, p, float(hit[0]), hit[1], equity)
                else:
                    _scale_out(tid, a, p, b, equity)
                    _trail(p, b)
            for o in [o for o in a["pending"] if o["meta"].get("yahoo", o["symbol"]) == sym and o["kind"] != "option"]:
                if dt.datetime.fromisoformat(o["created"]) > ts:
                    continue  # orders only fill on bars that start after they were placed
                if o.get("no_entry_after") and t_ny.time() >= core.hhmm(o["no_entry_after"]):
                    continue
                px = _entry_fill(o, b)
                if px is not None:
                    a["pending"] = [x for x in a["pending"] if x["id"] != o["id"]]
                    open_position(tid, a, o, float(px), ts, equity)


# ------------------------------------------------------------------ options (quote snapshots)
def manage_options(tid, a, equity, today):
    """Option fills and exits happen only in the regular session, on live quotes (before the open a quote is the last
    trade of the day before). An order with no live quote yet stays pending until it expires; expiry settles any time."""
    rules = settings.rules(tid)
    live = core.market_open_at(core.ny())
    now = core.utcnow()
    for o in [o for o in a["pending"] if o["kind"] == "option"]:
        if dt.datetime.fromisoformat(o["valid_until"]) <= now:
            a["pending"] = [x for x in a["pending"] if x["id"] != o["id"]]
            core.log("skip", trader=tid, symbol=o["symbol"], reason="option order expired unfilled")
            continue
        if not live:
            continue
        m = o["meta"]
        q = data.contract_quote(m["contract"], m["underlying"], m["expiry"], m["right"], m["strike"])
        fx = m.get("fx", 1.0)
        px = ((q["bid"] if o.get("action") == "SELL_TO_OPEN" else (q["ask"] or (q.get("last") or 0) * 1.05)) * fx) if q else 0
        if not px or px <= 0:
            if not o.get("no_quote"):
                core.log("skip", trader=tid, symbol=o["symbol"], reason="no live option quote yet: the order stays pending")
                o["no_quote"] = True
            continue
        a["pending"] = [x for x in a["pending"] if x["id"] != o["id"]]
        open_position(tid, a, o, px, core.utcnow(), equity)
    for p in [p for p in a["positions"] if p["kind"] == "option"]:
        m = p["meta"]
        exp = dt.date.fromisoformat(m["expiry"])
        if today > exp:
            _settle_expiry(tid, a, p, equity)
            continue
        if not live:
            continue
        q = data.contract_quote(m["contract"], m["underlying"], m["expiry"], m["right"], m["strike"])
        fx = m.get("fx", 1.0)
        if q:
            q = {k: v * fx for k, v in q.items()}
        if not q:
            continue
        mid = (q["bid"] + q["ask"]) / 2 if q["ask"] else q["last"]
        p["mark"] = mid
        if p["side"] == "long":
            chg = 100 * (mid / p["entry"] - 1) if p["entry"] else 0
            if p.get("runner") and chg <= 0:
                close_position(tid, a, p, q["bid"] or mid, "runner back to breakeven: closed", equity)
            elif p.get("runner") and chg >= rules.get("runner_take_pct", 300):
                close_position(tid, a, p, q["bid"] or mid, f"runner take profit (+{chg:.0f}%)", equity)
            elif chg >= rules.get("take_profit_pct", 1e9) and not p.get("runner") and rules.get("partial_take") and p["qty"] >= 2:
                partial_close(tid, a, p, p["qty"] // 2, q["bid"] or mid, f"took half at +{chg:.0f}%; rest is a runner", equity)
                p["runner"] = True
            elif chg >= rules.get("take_profit_pct", 1e9) and not p.get("runner"):
                close_position(tid, a, p, q["bid"] or mid, f"take profit (+{chg:.0f}%)", equity)
            elif chg <= rules.get("stop_pct", -1e9):
                close_position(tid, a, p, q["bid"] or mid, f"option stop ({chg:.0f}%)", equity)
            elif (exp - today).days <= rules.get("exit_days_before_expiry", 0):
                close_position(tid, a, p, q["bid"] or mid, "close before expiry", equity)
        else:
            kept = 100 * (1 - mid / p["entry"]) if p["entry"] else 0
            if kept >= rules.get("take_profit_pct", 1e9):
                close_position(tid, a, p, q["ask"] or mid, f"bought back after keeping {kept:.0f}% of the premium", equity)


def _settle_expiry(tid, a, p, equity):
    m = p["meta"]
    under = data.daily([m["underlying"]], "5d").get(m["underlying"])
    px = float(under["close"].iloc[-1]) if under is not None and len(under) else m["strike"]
    itm = (px > m["strike"]) if m["right"] == "C" else (px < m["strike"])
    fx = m.get("fx", 1.0)
    if p["side"] == "long":
        intrinsic = max(0.0, (px - m["strike"]) if m["right"] == "C" else (m["strike"] - px)) * fx
        close_position(tid, a, p, intrinsic, "expired" + (" in the money" if itm else " worthless"), equity)
        return
    if not itm:
        close_position(tid, a, p, 0.0, "expired worthless: premium kept", equity)
        return
    # assignment
    close_position(tid, a, p, 0.0, "assigned at expiry", equity)
    n = p["qty"] * MULT_OPTION
    if m["right"] == "P":
        o = {"id": core.new_id(a, "as"), "kind": "stock", "symbol": m["underlying"], "qty": n, "side": "long",
             "why": f"assigned on the ${m['strike']:.2f} put: bought {n} shares at the strike", "source": "assignment",
             "meta": {"wheel": True}}
        open_position(tid, a, o, m["strike"] * fx, core.utcnow(), equity)
    else:
        for s in [s for s in a["positions"] if s["kind"] == "stock" and s["symbol"] == m["underlying"]]:
            close_position(tid, a, s, m["strike"] * fx, f"called away at US${m['strike']:.2f}", equity)


# ------------------------------------------------------------------ marking
def mark(a, prices):
    pos_val = 0.0
    for p in a["positions"]:
        k = p["kind"]
        px = prices.get(p["meta"].get("yahoo", p["symbol"]))
        if k == "option":
            v = p.get("mark", p["entry"]) * MULT_OPTION * p["qty"]
            pos_val += -v if p["side"] == "short" else v
        elif k == "future":
            if px is not None:
                sign = 1 if p["side"] == "long" else -1
                p["unrealized"] = sign * p["qty"] * (px - p["entry"]) * p["meta"]["multiplier"]
            pos_val += p.get("unrealized", 0)
        else:
            if px is not None:
                p["last"] = px
            pos_val += p["qty"] * p.get("last", p["entry"])
    return pos_val, a["cash"] + pos_val
