"""The seven traders' rulebooks. Each turns the morning meeting's plan (if there is one) plus the scanner into
paper orders with a what, a why and a how-much. With no meeting that day, the rules run on autopilot."""
import datetime as dt
import json
import math

import pandas as pd

from . import core, data, edge, settings
from .engine import buying_power

R = settings.rules
CA = (".TO", ".V")


def cad(sym, px, fx):
    """Native price -> C$ (US-listed, futures and crypto are in US$)."""
    return px if px is None or sym.endswith(CA) else px * fx


def ranked(tid, rows, key=lambda r: 0):
    """Order candidates by the trader's industry focus (sector scale); 0 = excluded."""
    out = []
    for i, r in enumerate(rows):
        m = settings.sector_mult(tid, data.info(r["symbol"]).get("sector")) if r.get("symbol") else 1.0
        if m > 0:
            out.append(((1.0 / (i + 1) + key(r)) * m, i, r))
    return [r for _, _, r in sorted(out, key=lambda x: (-x[0], x[1]))]

T = core.CFG["traders"]


def order(a, kind, symbol, qty, why, source="autopilot", entry_type="market", price=None, stop=None, target=None,
          side="long", meta=None, valid_hours=8, **extra):
    now = core.utcnow()
    o = {"id": core.new_id(a, "o"), "kind": kind, "symbol": symbol, "qty": qty, "side": side, "entry_type": entry_type,
         "price": price, "stop": stop, "target": target, "why": why, "source": source, "meta": meta or {},
         "created": now.isoformat(), "valid_until": (now + dt.timedelta(hours=valid_hours)).isoformat()}
    o.update(extra)
    return o


def held(a, sym):
    return any(p["symbol"] == sym for p in a["positions"]) or any(o["symbol"] == sym for o in a["pending"])


def _once(a, key, date):
    """True the first time a daily task runs for this trader on this date."""
    done = a.setdefault("done", {})
    if done.get(key) == date:
        return False
    done[key] = date
    return True


def _scan(ctx, name):
    return ctx.get("premarket", {}).get("lists", {}).get(name, [])


def _size(equity, bp, entry, risk_per_unit, risk_pct, cap_pct, unit=1.0, whole=True):
    if entry <= 0:
        return 0
    by_risk = (equity * risk_pct / 100) / risk_per_unit if risk_per_unit > 0 else 1e18
    q = min(by_risk, equity * cap_pct / 100 / (entry * unit), max(bp, 0) / (entry * unit))
    return core.whole(q) if whole else max(0.0, q)


# ------------------------------------------------------------------ meeting plan orders
def plan_orders(tid, a, ctx):
    plan = ctx.get("plan") or {}
    mine = (plan.get("traders") or {}).get(tid) or {}
    out, date = [], ctx["date"]
    used = a.setdefault("plan_used", {}).setdefault(date, [])
    for i, p in enumerate(mine.get("orders", [])):
        if i in used:
            continue
        used.append(i)
        why = "Meeting: " + p.get("why", "")
        kind = p.get("kind", "stock")
        if kind == "option":
            o = option_order(tid, a, ctx, p["symbol"], p.get("right", "C"), p.get("action", "BUY_TO_OPEN"),
                             p.get("dte", [7, 30]), p.get("otm_pct", 0.03), why, "meeting", p.get("contracts"))
            if o:
                out.append(o)
            continue
        fx = ctx["fx"]
        if kind == "future":
            fsym = T[tid]["contracts"].get(p["symbol"], {}).get("yahoo", p["symbol"])
        else:
            fsym = p["symbol"]
        for k in ("price", "stop", "target"):
            if p.get(k) is not None:
                p = {**p, k: cad(fsym, float(p[k]), fx)}
        px = p.get("price") or ctx["prices"].get(fsym) or cad(fsym, data.last_price(fsym), fx)
        if not px:
            core.log("skip", trader=tid, symbol=p["symbol"], reason="no price for meeting order")
            continue
        stop = p.get("stop")
        risk = (px - stop) if stop else px * 0.1
        if kind == "crypto":
            usd = p.get("usd") or ctx["equity"] * p.get("pct", 10) / 100
            qty = round(min(usd, buying_power(a)) / px, 8)
        elif kind == "future":
            spec = T[tid]["contracts"][p["symbol"]]
            qty = int(p.get("qty", 1))
            o = order(a, "future", p["symbol"], qty, why, "meeting", p.get("entry_type", "market"), p.get("price"),
                      stop, p.get("target"), p.get("side", "long"), {**spec, "yahoo": spec["yahoo"]}, valid_hours=24)
            out.append(o)
            continue
        else:
            r = R(tid)
            qty = p.get("qty") or _size(ctx["equity"], buying_power(a), px, risk, p.get("risk_pct", r.get("risk_pct", 2)),
                                         r.get("max_position_pct", 40))
        if qty:
            out.append(order(a, kind, p["symbol"], qty, why, "meeting", p.get("entry_type", "market"), p.get("price"),
                             stop, p.get("target"), p.get("side", "long"), {"small_cap": tid == "day"},
                             flat_by=R(tid).get("flat_by") if tid == "day" else None,
                             no_entry_after=R(tid).get("no_entry_after") if tid == "day" else None))
    return out


# ------------------------------------------------------------------ 1. day trader
def day(tid, a, ctx):
    r, t = R(tid), ctx["now_ny"]
    if not core.market_open_at(t) or t.time() < dt.time(9, 45) or t.time() >= core.hhmm(r["no_entry_after"]):
        return []
    if a["day"]["trades"] + len(a["pending"]) >= r["max_trades_day"]:
        return []
    today = [x for x in core.read_trades() if x["trader"] == tid and x["ts"][:10] == ctx["date"]]
    losses = sum(1 for x in today if x.get("pnl") not in ("", None) and float(x["pnl"]) < 0)
    if losses >= r.get("max_losses_day", 2):   # Dax rule 3a: two losers and he's done for the day
        if _once(a, "dax_done", ctx["date"]):
            core.log("skip", trader=tid, symbol="*", reason=f"{losses} losing trades today: Dax stops for the day")
        return []
    lunch = r.get("lunch", ["11:30", "13:30"])  # Dax rule 3b: no new entries in the lunch lull
    if core.hhmm(lunch[0]) <= t.time() < core.hhmm(lunch[1]):
        return []
    traded = {x["symbol"] for x in today}
    out = []
    rows = []
    for row in ctx.get("intraday", {}).get("rows", []):
        s = row["symbol"]
        if s in traded or held(a, s) or not (r["price_min"] <= row["price"] <= r["price_max"]):
            continue
        if row["gap_pct"] < r["gap_min_pct"] or row["rel_vol"] < r["rel_vol_min"] or row["price"] < row["or_low"]:
            continue
        if row.get("vwap") and row["price"] < row["vwap"]:   # Dax rule 1: only above VWAP (buyers in control)
            continue
        rows.append(row)
    for row in rows[:12]:   # Dax rule 2: small float + heavy short interest ranks first (squeeze fuel)
        i = data.info(row["symbol"])
        fl, si = i.get("floatShares") or 0, i.get("shortPercentOfFloat") or 0
        row["float_m"], row["short_pct"] = fl / 1e6 if fl else None, si
        row["squeeze"] = (1.0 if fl and fl < r.get("low_float_m", 50) * 1e6 else 0) + (1.0 if si >= r.get("high_short", 0.15) else 0)
    rows.sort(key=lambda x: (-x.get("tabled", False), -x.get("squeeze", 0), -x["rel_vol"] * abs(x["gap_pct"])))
    for row in rows:
        if len(out) + a["day"]["trades"] + len(a["pending"]) >= r["max_trades_day"]:
            break
        s = row["symbol"]
        if s in traded or held(a, s) or not (r["price_min"] <= row["price"] <= r["price_max"]):
            continue
        if row["gap_pct"] < r["gap_min_pct"] or row["rel_vol"] < r["rel_vol_min"] or row["price"] < row["or_low"]:
            continue
        entry = round(row["or_high"] + 0.01, 2)
        stop = round(row["or_low"], 2)
        risk = entry - stop
        if risk <= 0 or risk / entry > 0.15:
            continue
        qty = _size(ctx["equity"], buying_power(a), entry, risk, r["risk_pct"], r["max_position_pct"])
        if qty < 1:
            continue
        sq = row.get("squeeze", 0)
        extra = (f" Float {row['float_m']:.0f}M, short interest {row['short_pct']:.0%}: squeeze candidate."
                 if sq and row.get("float_m") else "")
        why = (f"{s} gapped {row['gap_pct']:+.0f}% on {row['rel_vol']:.0f}x normal volume and is above VWAP "
               f"(C${row.get('vwap', 0):.2f}); buy a break above the 15-min high C${row['or_high']:.2f}, stop under the "
               f"range low C${stop:.2f}, half off at 1R, target {r['target_r']}R.{extra}")
        if row.get("tabled"):
            why = "Tabled at the meeting. " + why
        out.append(order(a, "stock", s, qty, why, "meeting" if row.get("tabled") else "autopilot", "stop", entry, stop,
                         round(entry + r["target_r"] * risk, 2), meta={"small_cap": True}, valid_hours=6,
                         flat_by=r["flat_by"], no_entry_after=r["no_entry_after"],
                         play="Squeeze ORB" if row.get("squeeze") else "ORB+VWAP"))
    return out


# ------------------------------------------------------------------ 2. swing trader
ROTATION = {  # Sam rule 2: where money goes when the 10-year yield moves fast
    "rising": {"Financial Services": 1.5, "Energy": 1.5, "Industrials": 1.2, "Technology": 0.7, "Real Estate": 0.6,
               "Utilities": 0.7, "Communication Services": 0.8},
    "falling": {"Technology": 1.5, "Communication Services": 1.3, "Real Estate": 1.3, "Consumer Cyclical": 1.2,
                "Utilities": 1.2, "Financial Services": 0.8, "Energy": 0.7}}


def _earnings_gap_hold(sym, fx, min_gap, max_sessions):
    """Sam/Vega: reported in the last few sessions, gapped up, beat, and has held above the gap-day midpoint."""
    v = edge.earnings_view(sym)
    if not v.get("gap_pct") or v["gap_pct"] < min_gap or not v.get("held"):
        return None
    if not (1 <= v.get("sessions_since", 99) <= max_sessions) or (v.get("surprise_pct") is not None and v["surprise_pct"] <= 0):
        return None
    return {**v, "day_low_cad": cad(sym, v["day_low"], fx), "mid_cad": cad(sym, v["mid"], fx), "last_cad": cad(sym, v["last_close"], fx)}


def swing(tid, a, ctx):
    r, t = R(tid), ctx["now_ny"]
    if not core.market_open_at(t) or t.time() < dt.time(9, 45):
        return []
    slots = r["max_positions"] - len(a["positions"]) - len(a["pending"])
    window = "am" if t.time() < dt.time(13, 0) else "pm"
    if slots <= 0 or not _once(a, f"swing_pick_{window}", ctx["date"]):
        return []
    tnx = ctx.get("tnx_5d")
    trend = None if tnx is None else "rising" if tnx > r.get("rate_move", 0.15) else "falling" if tnx < -r.get("rate_move", 0.15) else None
    rate_note = "" if tnx is None else f" 10-yr yield {tnx:+.2f} pts in 5 days" + (f" ({trend}: rotation applied)" if trend else "")
    out, n_new, fx = [], min(slots, r.get("new_per_day", 3)), ctx["fx"]

    # Sam rule 1: earnings gap-and-hold (the post-earnings drift), checked first
    pool = list(dict.fromkeys([x["symbol"] for x in ctx.get("tabled_rows", []) + ctx.get("report_rows", [])
                               + _scan(ctx, "breakout_20d")[:12] + _scan(ctx, "momentum_options")[:8]]))
    for sym in pool[:r.get("earnings_checks", 18)]:
        if len(out) >= n_new or held(a, sym):
            continue
        g = _earnings_gap_hold(sym, fx, r.get("earn_gap_min", 4), r.get("earn_max_sessions", 3))
        if not g or g["last_cad"] <= g["day_low_cad"]:
            continue
        stop = round(g["day_low_cad"] * 0.995, 2)
        px = ctx["prices"].get(sym) or g["last_cad"]
        qty = _size(ctx["equity"], buying_power(a), px, px - stop, r["risk_pct"], r["max_position_pct"])
        if qty < 1:
            continue
        why = (f"{sym} reported {g['last']} ({'beat by ' + format(g['surprise_pct'], '.0f') + '%' if g.get('surprise_pct') else 'result'}), "
               f"gapped {g['gap_pct']:+.1f}% and has held above the gap-day midpoint for {g['sessions_since']} sessions. "
               f"Stop under the gap-day low C${stop:.2f}, half off at 1R, out in {r.get('earn_time_stop', 5)} days if the drift stalls.")
        out.append(order(a, "stock", sym, qty, why, "autopilot", "market", None, stop, round(px + 2 * (px - stop), 2),
                         time_stop=(t.date() + dt.timedelta(days=r.get("earn_time_stop", 5) + 2)).isoformat(),
                         play="Earnings gap-and-hold"))

    picks = [("Tabled at the meeting", x) for x in ctx.get("tabled_rows", [])]
    picks += [("Insight from the 4-a-day reports", x) for x in ctx.get("report_rows", [])]
    picks += [("20-day breakout", x) for x in _scan(ctx, "breakout_20d")[:15]]
    picks += [("Pullback in an uptrend", x) for x in _scan(ctx, "pullback_uptrend")[:15]]
    rot = ROTATION.get(trend, {})
    order_ = ranked(tid, [x for _, x in picks], key=lambda x: rot.get(data.info(x["symbol"]).get("sector"), 1.0) - 1)
    lab = {id(x): l for l, x in picks}
    picks = [(lab[id(x)], x) for x in order_]
    for label, x in picks:
        if len(out) >= n_new:
            break
        s = x["symbol"]
        if held(a, s) or any(o["symbol"] == s for o in out) or not x.get("atr") or x["price"] < 5:
            continue
        if edge.earnings_within(s, r["avoid_earnings_within_days"], t.date()):
            core.log("skip", trader=tid, symbol=s, reason="earnings too close: Sam trades the reaction, not the coin flip")
            continue
        news, h = "", None
        if label in ("20-day breakout", "Pullback in an uptrend"):  # Sam rule 3: a chart move needs a fresh headline
            h = edge.headlines(s, r.get("news_hours", 48))
            if h == []:
                continue
            news = f" News: \"{h[0][:90]}\"." if h else " (news feed unavailable: chart-only entry)"
        sector = data.info(s).get("sector") or ""
        rot_note = f" {sector} favoured while yields are {trend}." if rot.get(sector, 1) > 1 else ""
        stop = round(x["price"] - r["stop_atr"] * x["atr"], 2)
        qty = _size(ctx["equity"], buying_power(a), x["price"], x["price"] - stop, r["risk_pct"], r["max_position_pct"])
        if qty < 1:
            continue
        why = (f"{label}: {s} at C${x['price']:.2f}, {x.get('d5', 0) * 100:+.1f}% in 5 days, RSI {x.get('rsi', 0):.0f}, "
               f"volume {x.get('rel_vol', 0):.1f}x.{news}{rot_note} Stop {r['stop_atr']} ATR, half off at 1R, target "
               f"{r['target_atr']} ATR, out in {r['time_stop_days']} days if neither.{rate_note}")
        out.append(order(a, "stock", s, qty, why, "meeting" if label.startswith("Tabled") else "autopilot", "market",
                         None, stop, round(x["price"] + r["target_atr"] * x["atr"], 2),
                         time_stop=(t.date() + dt.timedelta(days=14)).isoformat(),
                         play="News breakout" if h and label == "20-day breakout" else "News pullback" if h else
                         "Rate rotation" if rot_note else label))
    return out


# ------------------------------------------------------------------ 3. long-term investor
def long(tid, a, ctx):
    r, t = R(tid), ctx["now_ny"]
    if not core.market_open_at(t) or t.time() < dt.time(10, 0):
        return []
    if not _once(a, "long_review", ctx["date"]) or (t.weekday() != 0 and a.get("seeded")):
        return []  # reviews on Mondays (and the first day)
    a["seeded"] = True
    out = []
    # Lena rule 1: tranches - the 2nd and 3rd thirds go in on later Mondays if the stock held up and still qualifies
    for sym, tr in list(a.setdefault("tranches", {}).items()):
        pos = [p for p in a["positions"] if p["symbol"] == sym]
        if not pos or tr["left"] <= 0:
            a["tranches"].pop(sym, None)
            continue
        px = ctx["prices"].get(sym) or cad(sym, data.last_price(sym), ctx["fx"])
        if px and px >= pos[0]["entry"] * (1 - r.get("tranche_max_drop", 0.03)) and (data.info(sym).get("revenueGrowth") or 0) > 0:
            q = min(tr["per"], core.whole(buying_power(a) / px))
            if q >= 1:
                tr["left"] -= 1
                out.append(order(a, "stock", sym, q, f"Tranche {3 - tr['left']} of 3: {sym} held up since the first buy "
                                 f"(C${pos[0]['entry']:.2f} -> C${px:.2f}) and still grows revenue.",
                                 stop=pos[0].get("stop"), meta={"yahoo": sym}, valid_hours=8, play="Tranche add"))
    for p in [p for p in a["positions"] if p["kind"] == "stock"]:
        i = data.info(p["symbol"])
        if (i.get("revenueGrowth") or 0) < 0:
            out.append({"close": p["id"], "why": f"Quarterly review: {p['symbol']} revenue now shrinking ({i.get('revenueGrowth'):+.1%})"})
    slots = r["max_positions"] - len(a["positions"]) - len(a["pending"])
    if slots <= 0:
        return out
    pool = list(dict.fromkeys([x["symbol"] for x in ctx.get("tabled_rows", [])] + T[tid].get("universe", []) +
                              [x["symbol"] for x in _scan(ctx, "pullback_uptrend")[:20]]))
    d = data.daily(pool, "1y")
    scored = []
    for s in pool:
        df = d.get(s)
        if df is None or len(df) < 200 or held(a, s):
            continue
        last, s50 = float(df["close"].iloc[-1]), float(df["close"].rolling(50).mean().iloc[-1])
        s200 = df["close"].rolling(200).mean()
        if not (s50 * 0.88 <= last <= s50 * (1 + r["buy_vs_50d_max"])) or last < float(s200.iloc[-1]) * 0.97 \
                or float(s200.iloc[-1]) < float(s200.iloc[-21]):
            continue
        scored.append((s, last, s50))
    best = []
    for s, last, s50 in scored[:40]:
        i = data.info(s)
        g, m, y = i.get("revenueGrowth") or 0, i.get("operatingMargins") or 0, (i.get("dividendYield") or 0) / 100
        fpe, peg = i.get("forwardPE"), i.get("trailingPegRatio") or i.get("pegRatio")
        if (fpe and fpe > r.get("max_forward_pe", 45)) or (peg and peg > r.get("max_peg", 3)):
            continue   # Lena rule 3a: growth, but not at any price
        if edge.earnings_within(s, r.get("pre_earnings_wait_days", 7), t.date()):
            core.log("skip", trader=tid, symbol=s, reason="reports within a week: Lena waits for the quarter")
            continue   # Lena rule 3b: never buy the week before a report
        if r["min_revenue_growth"] <= g <= 1.5 and m >= r["min_operating_margin"] and (i.get("marketCap") or 0) >= 3e9:
            sm = settings.sector_mult(tid, i.get("sector"))
            if sm > 0:
                grower = _dividend_grower(s)   # Lena rule 2: dividend raisers score higher
                best.append(((g + m / 2 + y * 3 + (0.05 if y > 0 else 0) + (0.1 if grower else 0)) * sm, s, last, s50, g, m, y,
                             i.get("shortName", s) + (" (raised its dividend this year)" if grower else "")))
    best.sort(reverse=True)
    per = ctx["equity"] / r["max_positions"]
    for score, s, last, s50, g, m, y, name in best[:slots]:
        px = cad(s, last, ctx["fx"])
        full = core.whole(min(per, buying_power(a)) / px)
        qty = max(1, math.ceil(full / 3)) if full >= 1 else 0
        if qty < 1:
            continue
        if full > qty:
            a["tranches"][s] = {"left": 2, "per": max(1, (full - qty) // 2)}
        why = (f"{name}: revenue {g:+.0%}, operating margin {m:.0%}, dividend {y:.1%}; bought on a pullback to its rising "
               f"50-day average ({last / s50 - 1:+.1%}). Held for the long term; reviewed each quarter after earnings. "
               f"Safety stop {abs(r.get('stop_pct', 18)):g}% below. First third now; the rest over the next two Mondays if it holds.")
        out.append(order(a, "stock", s, qty, why, stop=round(px * (1 - abs(r.get("stop_pct", 18)) / 100), 2),
                         meta={"yahoo": s, "fx": s.endswith(".TO")}, valid_hours=8,
                         play="Dividend grower" if "raised its dividend" in name else "Quality pullback"))
    return out


def _dividend_grower(sym):
    """True if the last 12 months of dividends beat the 12 months before (a raise)."""
    try:
        import yfinance as yf
        dv = yf.Ticker(sym).dividends
        if dv is None or len(dv) < 4:
            return False
        dv.index = dv.index.tz_localize(None) if dv.index.tz is not None else dv.index
        now = pd.Timestamp(core.utcnow())
        a1 = dv[dv.index > now - pd.Timedelta(days=365)].sum()
        a0 = dv[(dv.index <= now - pd.Timedelta(days=365)) & (dv.index > now - pd.Timedelta(days=730))].sum()
        return bool(a0 > 0 and a1 > a0 * 1.01)
    except Exception:
        return False


# ------------------------------------------------------------------ 4. futures & metals
def futures(tid, a, ctx):
    r, cs = R(tid), T[tid]["contracts"]
    if not _once(a, "fut_daily", ctx["date"]):
        return []
    a["pending"] = [o for o in a["pending"] if o["kind"] != "future"]  # yesterday's breakout orders expire
    raw = data.daily([c["yahoo"] for c in cs.values()] + ["DX-Y.NYB"], "1y")
    dxy = raw.pop("DX-Y.NYB", None)
    d = {k: v.assign(**{c: v[c] * ctx["fx"] for c in ("open", "high", "low", "close")}) for k, v in raw.items()}
    out = []
    margin_used = sum(p.get("margin", 0) for p in a["positions"])

    def atr_of(df):
        cl = df["close"]
        tr = pd.concat([df["high"] - df["low"], (df["high"] - cl.shift()).abs(), (df["low"] - cl.shift()).abs()], axis=1).max(axis=1)
        return float(tr.rolling(14).mean().iloc[-1])

    # Fox rule 3: chandelier exit - the stop trails 3 ATR behind the best price since entry, so trends can run
    # Fox rule 2: pyramiding - one more contract once a trade is 1.5 ATR in profit, if margin allows
    for p in [p for p in a["positions"] if p["kind"] == "future"]:
        df = d.get(p["meta"].get("yahoo"))
        if df is None or len(df) < 20:
            continue
        atr, long_ = atr_of(df), p["side"] == "long"
        since = df[df.index >= pd.Timestamp(p["entry_ts"][:10])]
        if len(since):
            if long_:
                ch = float(since["high"].max()) - r.get("chandelier_atr", 3) * atr
                if ch > (p.get("stop") or -1e18):
                    p["stop"] = round(ch, 4)
            else:
                ch = float(since["low"].min()) + r.get("chandelier_atr", 3) * atr
                if ch < (p.get("stop") or 1e18):
                    p["stop"] = round(ch, 4)
        last = float(df["close"].iloc[-1])
        gain = (last - p["entry"]) if long_ else (p["entry"] - last)
        lots = [x for x in a["positions"] if x["symbol"] == p["symbol"]]
        if gain >= r.get("pyramid_atr", 1.5) * atr and len(lots) < 1 + r.get("max_adds", 1) and not p.get("pyramided"):
            spec = cs[p["symbol"]]
            m1 = last * spec["multiplier"] * r["margin_pct"]
            if margin_used + m1 <= ctx["equity"] * 0.9:
                p["pyramided"] = True
                margin_used += m1
                out.append(order(a, "future", p["symbol"], 1, f"Pyramid: {spec['name']} is {gain / atr:.1f} ATR in profit; "
                                 f"add 1 contract with its stop at the first entry (C${p['entry']:,.2f}).", entry_type="market",
                                 stop=p["entry"], target=None, side=p["side"], meta={**spec, "yahoo": spec["yahoo"]},
                                 valid_hours=24, play="Pyramid add"))
    dx_trend = None
    if dxy is not None and len(dxy) > 30:
        e20 = dxy["close"].ewm(span=20).mean()
        dx_trend = float(e20.iloc[-1] / e20.iloc[-6] - 1)   # 5-day slope of the dollar's 20-day EMA
    for code, c in cs.items():
        df = d.get(c["yahoo"])
        if df is None or len(df) < 60 or any(p["symbol"] == code for p in a["positions"]):
            continue
        cl = df["close"]
        e20, e50 = cl.ewm(span=20).mean().iloc[-1], cl.ewm(span=50).mean().iloc[-1]
        last = float(cl.iloc[-1])
        tr = pd.concat([df["high"] - df["low"], (df["high"] - cl.shift()).abs(), (df["low"] - cl.shift()).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        hi, lo = float(df["high"].iloc[-21:-1].max()), float(df["low"].iloc[-21:-1].min())
        side = "long" if last > e50 and e20 > e50 else "short" if last < e50 and e20 < e50 else None
        if not side:
            continue
        dx_note = ""
        if code in r.get("dollar_sensitive", ["MGC", "SIL"]) and dx_trend is not None:   # Fox rule 1: metals vs the US dollar
            if side == "long" and dx_trend > r.get("dxy_tolerance", 0.002):
                core.log("skip", trader=tid, symbol=code, reason=f"dollar rising ({dx_trend:+.2%}): no metal longs")
                continue
            if side == "short" and dx_trend < -r.get("dxy_tolerance", 0.002):
                core.log("skip", trader=tid, symbol=code, reason=f"dollar falling ({dx_trend:+.2%}): no metal shorts")
                continue
            dx_note = f" US dollar {'falling' if dx_trend < 0 else 'firm'} ({dx_trend:+.2%} on its 20-day trend) agrees."
        trig = hi if side == "long" else lo
        m1 = trig * c["multiplier"] * r["margin_pct"]
        n = min(r["max_contracts"], core.whole((ctx["equity"] * 0.9 - margin_used) / m1)) if m1 else 0
        if n < 1:
            core.log("skip", trader=tid, symbol=code, reason=f"margin ${m1:,.0f} per contract is more than free equity")
            continue
        margin_used += n * m1
        sign = 1 if side == "long" else -1
        why = (f"{c['name']} trend is {'up' if side == 'long' else 'down'} (20-day EMA {'above' if side == 'long' else 'below'} "
               f"the 50-day). {'Buy' if side == 'long' else 'Sell short'} {n} on a break of the 20-day "
               f"{'high' if side == 'long' else 'low'} US${trig / ctx['fx']:,.2f}; stop {r['stop_atr']} ATR, then a "
               f"{r.get('chandelier_atr', 3)} ATR chandelier trail (no fixed target: let the trend run).{dx_note} "
               f"Notional C${n * trig * c['multiplier']:,.0f} on C${ctx['equity']:,.0f} equity.")
        out.append(order(a, "future", code, n, why, entry_type="stop", price=trig, stop=round(trig - sign * r["stop_atr"] * atr, 4),
                         target=None, side=side, meta={**c, "yahoo": c["yahoo"]}, valid_hours=24,
                         play="Metal trend + dollar" if dx_note else "Trend breakout"))
    return out


# ------------------------------------------------------------------ 5/6. options
def option_order(tid, a, ctx, sym, right, action, dte, otm, why, source, contracts=None):
    exp = data.pick_expiry(sym, dte[0], dte[1], ctx["now_ny"].date())
    px = data.last_price(sym)
    fx = ctx["fx"]
    if not exp or not px:
        return None
    target = px * (1 + otm) if right == "C" else px * (1 - otm)
    c = data.pick_contract(sym, exp, right, target)
    if not c:
        return None
    prem = c["bid"] if action == "SELL_TO_OPEN" else (c["ask"] or c["last"])
    if prem <= 0:
        return None
    if contracts is None:
        if action == "SELL_TO_OPEN":
            contracts = 1
        else:
            contracts = core.whole(min(ctx["equity"] * R(tid).get("risk_pct", 10) / 100, buying_power(a)) / (prem * 100 * fx))
    if contracts < 1:
        return None
    meta = {"underlying": sym, "expiry": exp, "right": right, "strike": c["strike"], "contract": c["contract"], "iv": c["iv"],
            "fx": fx}
    try:
        why = why.format(**c, exp=exp, prem=prem, px=px)
    except (KeyError, ValueError, IndexError):
        pass
    o = order(a, "option", f"{sym} {exp} {c['strike']:g}{right}", contracts, why,
              source, meta=meta, action=action)
    if action == "SELL_TO_OPEN" and right == "P":
        o["cash_secured"] = True
        o["reserve"] = contracts * 100 * c["strike"] * fx
    if action == "SELL_TO_OPEN" and right == "C":
        o["covered"] = True
    return o


def options_aggr(tid, a, ctx):
    r, t = R(tid), ctx["now_ny"]
    if not core.market_open_at(t) or t.time() < dt.time(10, 0):
        return []
    if len(a["positions"]) + len(a["pending"]) >= r["max_positions"] or not _once(a, "oa_pick", ctx["date"]):
        return []
    pool = ranked(tid, ctx.get("tabled_rows", []) + _scan(ctx, "momentum_options")[:12])
    out = []
    max_iv = r.get("max_iv", 1.2)

    def cheap_enough(o):   # Vega rule 1: never overpay for volatility
        if o and (o["meta"].get("iv") or 0) > max_iv:
            core.log("skip", trader=tid, symbol=o["symbol"], reason=f"implied volatility {o['meta']['iv']:.0%} > {max_iv:.0%}: too expensive")
            return None
        return o

    # Vega rule 2: calls on an earnings gap that held (the same tree Sam uses, with leverage)
    for x in pool[:r.get("earnings_checks", 10)]:
        s = x["symbol"]
        if s.endswith(CA) or any(p["meta"].get("underlying") == s for p in a["positions"]):
            continue
        g = _earnings_gap_hold(s, ctx["fx"], r.get("earn_gap_min", 5), r.get("earn_max_sessions", 3))
        if not g:
            continue
        why = (f"{s} gapped {g['gap_pct']:+.1f}% on earnings ({g['last']}) and held above the gap-day midpoint. "
               f"Buy the {{exp}} ${{strike:g}} call at ${{prem:.2f}} (stock ${{px:.2f}}). Half off at +{r['take_profit_pct']:g}%, "
               f"the rest runs with a breakeven stop. Max loss = premium paid.")
        for otm in (r["otm_pct"], r["otm_pct"] * 2):
            o = cheap_enough(option_order(tid, a, ctx, s, "C", "BUY_TO_OPEN", [r["dte_min"], r["dte_max"]], otm, why, "autopilot"))
            if o:
                o["play"] = "Earnings call"
                return [o]
    for x in pool:
        s = x["symbol"]
        if s.endswith((".TO", ".V")) or any(p["meta"].get("underlying") == s for p in a["positions"]):
            continue
        bull = (x.get("d5") or 0) > 0 and x["price"] > (x.get("sma20") or 0)
        bear = (x.get("d5") or 0) < 0 and x["price"] < (x.get("sma20") or 1e18)
        if not (bull or bear):
            continue
        right = "C" if bull else "P"
        why = (f"{s} {'ripping' if bull else 'breaking down'}: {x.get('d5', 0) * 100:+.1f}% in 5 days on {x.get('rel_vol', 0):.1f}x volume. "
               f"Buy the {{exp}} ${{strike:g}} {'call' if bull else 'put'} at ${{prem:.2f}} (stock ${{px:.2f}}). "
               f"Half off at +{r['take_profit_pct']:g}%, the rest rides to +{r.get('runner_take_pct', 300):g}% or back to breakeven; "
               f"cut at {r['stop_pct']:g}%. Max loss = premium paid.")
        o = None
        for otm in (r["otm_pct"], r["otm_pct"] * 2, r["otm_pct"] * 3.5):  # further out of the money until it fits the budget
            o = cheap_enough(option_order(tid, a, ctx, s, right, "BUY_TO_OPEN", [r["dte_min"], r["dte_max"]], otm, why,
                                          "meeting" if x in ctx.get("tabled_rows", []) else "autopilot"))
            if o:
                break
        if o:
            o["play"] = "Momentum call" if right == "C" else "Momentum put"
            out.append(o)
            break
    return out


def options_safe(tid, a, ctx):
    r, t = R(tid), ctx["now_ny"]
    if not core.market_open_at(t) or t.time() < dt.time(10, 0) or not _once(a, "os_pick", ctx["date"]):
        return []
    stocks = [p for p in a["positions"] if p["kind"] == "stock"]
    calls = {p["meta"]["underlying"] for p in a["positions"] if p["kind"] == "option" and p["meta"]["right"] == "C"}
    out = []
    for s in stocks:  # wheel step 2: covered call on assigned shares
        if s["symbol"] in calls or s["qty"] < 100 or s["symbol"] == r.get("cash_etf"):
            continue
        why = (f"Wheel step 2: own {s['qty']:g} {s['symbol']} (cost ${s['entry']:.2f}); sell the {{exp}} ${{strike:g}} covered call "
               f"for ${{prem:.2f}} a share. Keep the premium; shares are sold at the strike if it closes above.")
        o = option_order(tid, a, ctx, s["symbol"], "C", "SELL_TO_OPEN", [r["dte_min"], r["dte_max"]], r["call_otm_pct"], why, "autopilot",
                         contracts=int(s["qty"] // 100))
        if o:
            out.append(o)
    park = r.get("cash_etf", "CASH.TO")
    parked = [p for p in stocks if p["symbol"] == park]
    stocks = [p for p in stocks if p["symbol"] != park]
    if any(p["kind"] == "option" and p["meta"]["right"] == "P" for p in a["positions"]) or stocks:
        return out
    fx = ctx["fx"]
    cheap = [x["symbol"] for x in _scan(ctx, "pullback_uptrend") + _scan(ctx, "breakout_20d")
             if not x["symbol"].endswith(CA) and x["price"] / fx <= r["max_strike"] / (1 - r["put_otm_pct"])
             and x.get("dollar_vol", 0) >= r.get("min_dollar_vol", 5e7)]
    pool = list(dict.fromkeys([x["symbol"] for x in ctx.get("tabled_rows", []) if x["price"] / fx <= r["max_strike"] * 1.1]
                              + r["universe"] + cheap))
    d = data.daily(pool, "1y")
    best = None
    cash_avail = buying_power(a) + sum(p["qty"] * ctx["prices"].get(park, p["entry"]) for p in parked)
    mood = (ctx.get("regime") or {}).get("mode", "neutral")
    if mood == "risk_off":   # Theo rule 3: fearful market -> half the cash at risk and strikes further away
        cash_avail *= r.get("risk_off_cash", 0.5)
    dte_mid = (r["dte_min"] + r["dte_max"]) / 2
    for s in [s for s in pool if s in d][:25]:
        df = d.get(s)
        if df is None or len(df) < 200:
            continue
        last = float(df["close"].iloc[-1])
        if last > r["max_strike"] / (1 - r["put_otm_pct"]) or last < float(df["close"].rolling(200).mean().iloc[-1]):
            continue
        if edge.earnings_within(s, r["dte_max"], t.date()):   # Theo rule 1: no put across an earnings report
            core.log("skip", trader=tid, symbol=s, reason="reports before the put would expire")
            continue
        # Theo rule 2: strike from the stock's own volatility, ~0.85 standard deviations out (about a 20% chance of assignment)
        hv = float(df["close"].pct_change().iloc[-30:].std() * math.sqrt(252))
        otm = min(max(hv * math.sqrt(dte_mid / 365) * r.get("sd_out", 0.85), r["put_otm_pct"]), 0.25)
        if mood == "risk_off":
            otm = min(otm * r.get("risk_off_otm_mult", 1.5), 0.3)
        exp = data.pick_expiry(s, r["dte_min"], r["dte_max"], t.date())
        c = data.pick_contract(s, exp, "P", last * (1 - otm)) if exp else None
        if not c or c["bid"] <= 0 or c["strike"] * 100 * ctx["fx"] > cash_avail:
            continue
        yld = c["bid"] / c["strike"]
        if yld < r.get("min_yield", 0.006):
            continue
        if not best or yld > best[0]:
            best = (yld, s, c["strike"] * 100 * ctx["fx"], otm, hv)
    if best and parked and best[2] > buying_power(a):  # free the parked cash first; the put goes on next run
        for p in parked:
            out.append({"close": p["id"], "why": f"Selling the {park} cash parking to secure a put on {best[1]}"})
        return out
    if not best and not parked and _once(a, "os_park", ctx["date"]):
        px = ctx["prices"].get(park) or data.last_price(park)
        q = core.whole(buying_power(a) * 0.98 / px) if px else 0
        if q >= 1:
            out.append(order(a, "stock", park, q, f"No cash-secured put qualifies today (needs a stock under US${r['max_strike']} "
                             f"above its 200-day). Idle cash parked in {park}, a high-interest savings ETF, until one does.",
                             stop=round(px * 0.97, 2), meta={"yahoo": park}, valid_hours=8, play="Cash parking"))
        return out
    if best:
        mood = (ctx.get("regime") or {}).get("mode", "neutral")
        why = ("Wheel step 1: {underlying} is above its 200-day and doesn't report before expiry; sell the {exp} ${strike:g} "
               "cash-secured put for ${prem:.2f} (stock ${px:.2f}). Strike set " + f"{best[3]:.0%} below, from its "
               f"{best[4]:.0%} volatility (about a 1-in-5 chance of assignment)"
               + (", further out because the market is fearful" if mood == "risk_off" else "") +
               ". ${strike:g} x 100 held in cash. If it closes below the strike we buy 100 shares there.")
        o = option_order(tid, a, ctx, best[1], "P", "SELL_TO_OPEN", [r["dte_min"], r["dte_max"]], best[3],
                         why.replace("{underlying}", best[1]), "autopilot")
        if o:
            o["play"] = "Volatility-sized put"
            out.append(o)
    return out


# ------------------------------------------------------------------ 7. crypto
def crypto(tid, a, ctx):
    t, b, r = T[tid], T[tid]["buckets"], R(tid)
    eq = ctx["equity"]
    coins = sorted({c for v in b.values() for c in v["coins"]})
    d = data.daily(coins, "3mo")
    mom = {c: float(df["close"].iloc[-1] / df["close"].iloc[-4] - 1) for c, df in d.items() if len(df) > 30}
    m30 = {c: float(df["close"].iloc[-1] / df["close"].iloc[-31] - 1) for c, df in d.items() if len(df) > 31}
    up20 = {c for c, df in d.items() if len(df) > 20 and df["close"].iloc[-1] > df["close"].rolling(20).mean().iloc[-1]}
    px = {c: float(df["close"].iloc[-1]) * ctx["fx"] for c, df in d.items()}
    out = []
    bucket_of = lambda p: p.get("meta", {}).get("bucket")
    for p in a["positions"]:  # coins bought before stops were required get their bucket's stop
        if p["kind"] == "crypto" and not p.get("stop"):
            p["stop"] = round(p["entry"] * (1 - abs(r.get(f"{bucket_of(p)}_stop_pct", 30)) / 100), 8)
            core.log("stop_set", trader=tid, symbol=p["symbol"], stop=p["stop"], reason="every position carries a stop")

    # Kai rule 2: altcoin season gate - growth, news and meme coins only while ETH is beating BTC (ETH/BTC above its 20-day)
    alt_ok, alt_note = True, ""
    if "ETH-USD" in d and "BTC-USD" in d:
        ratio = (d["ETH-USD"]["close"] / d["BTC-USD"]["close"]).dropna()
        if len(ratio) > 20:
            alt_ok = bool(ratio.iloc[-1] > ratio.rolling(20).mean().iloc[-1])
            alt_note = f" ETH/BTC {'above' if alt_ok else 'below'} its 20-day: alt season {'on' if alt_ok else 'off'}."
    if not alt_ok and _once(a, "kai_alt_off", ctx["date"]):
        core.log("skip", trader=tid, symbol="alts", reason="ETH/BTC below its 20-day: no new growth, news or meme coins")
    # Kai rule 1: crypto Fear & Greed
    fg = edge.fear_greed()
    if fg is not None and fg >= r.get("greed_level", 75):
        for p in a["positions"]:  # extreme greed: tighten the trail on the speculative coins
            if bucket_of(p) in ("news", "meme") and p.get("trail_pct"):
                p["trail_pct"] = min(p["trail_pct"], r.get("greed_trail_pct", 8))

    def buy(c, usd, why, bucket, stop=None, take=None, play=None):
        if c not in px or usd < 10:
            return
        if bucket in ("growth", "news", "meme") and not alt_ok:
            return
        q = round(min(usd, buying_power(a)) / px[c], 8)
        stop = stop if stop else -abs(r.get(f"{bucket}_stop_pct", 30))  # every coin gets a stop; long-term buckets get a wide one
        if q <= 0:
            return
        trail = r.get(f"{bucket}_trail_pct")   # Kai rule 3: news and meme coins trail their stop behind the high
        out.append(order(a, "crypto", c, q, why + (f" Stop trails {trail:g}% under the high." if trail else "") + alt_note,
                         stop=round(px[c] * (1 + stop / 100), 8) if stop else None,
                         target=None if trail else (round(px[c] * (1 + take / 100), 8) if take else None),
                         meta={"bucket": bucket, "yahoo": c}, valid_hours=24, trail_pct=trail,
                         play=play or f"{bucket.title()} bucket"))

    if fg is not None and fg <= r.get("fear_level", 25) and _once(a, "kai_fear", ctx["date"]):
        for c in b["core"]["coins"]:   # extreme fear: add to BTC/ETH (others' panic is the core bucket's discount)
            buy(c, eq * r.get("fear_buy_pct", 5) / 100, f"Crypto Fear & Greed at {fg} (extreme fear): add "
                f"{r.get('fear_buy_pct', 5)}% to {c.split('-')[0]}.", "core", play="Extreme-fear buy")

    if _once(a, "crypto_core", ctx["date"]):
        for c, w in zip(b["core"]["coins"], b["core"]["split"]):
            have = sum(p["qty"] * px.get(c, 0) for p in a["positions"] if p["symbol"] == c and bucket_of(p) == "core")
            want = eq * b["core"]["pct"] / 100 * w
            if have < want * (1 - r["rebalance_drift"]) and not held(a, c):
                buy(c, want - have, f"Core bucket ({b['core']['pct']}%): hold {c.split('-')[0]} long term; top up to {w:.0%} of the core.", "core")
        growth_held = [p for p in a["positions"] if bucket_of(p) == "growth"]
        if not growth_held:
            top = sorted([c for c in b["growth"]["coins"] if c in m30 and c in up20], key=lambda c: -m30[c])[:3]
            if not top:
                core.log("skip", trader=tid, symbol="growth bucket", reason="no growth coin above its 20-day average; cash waits")
            for c in top:
                buy(c, eq * b["growth"]["pct"] / 100 / 3, f"Growth bucket: {c.split('-')[0]} has the best 30-day trend "
                    f"({m30[c]:+.0%}) of the growth coins; held, rotated monthly.", "growth")
    news_plan = [o for o in ((ctx.get("plan") or {}).get("traders", {}).get(tid, {}).get("orders", []))]
    if not any(bucket_of(p) == "news" for p in a["positions"]) and not news_plan and _once(a, "crypto_news", ctx["date"]):
        cand = sorted([c for c in b["news"]["coins"] if c in mom], key=lambda c: -mom[c])
        if cand and mom[cand[0]] > 0.03:
            c = cand[0]
            buy(c, eq * b["news"]["pct"] / 100, f"News bucket (autopilot): {c.split('-')[0]} is moving {mom[c]:+.0%} in 3 days, "
                f"the strongest of the news list. Stop {r['news_stop_pct']}%, take profit +{r['news_take_pct']}%.", "news",
                r["news_stop_pct"], r["news_take_pct"])
    greedy = fg is not None and fg >= r.get("greed_level", 75)   # extreme greed: no new meme coins
    if not greedy and not any(bucket_of(p) == "meme" for p in a["positions"]) and _once(a, "crypto_meme", ctx["date"]):
        cand = sorted([c for c in b["meme"]["coins"] if c in mom], key=lambda c: -mom[c])
        if cand and mom[cand[0]] > 0:
            c = cand[0]
            buy(c, eq * b["meme"]["pct"] / 100, f"Meme bucket: {c.split('-')[0]} has the hottest 3-day run ({mom[c]:+.0%}). "
                f"Lottery ticket: stop {r['meme_stop_pct']}%, take +{r['meme_take_pct']}%.", "meme", r["meme_stop_pct"], r["meme_take_pct"])
    return out


RULEBOOK = {"day": day, "swing": swing, "long": long, "futures": futures, "options_aggr": options_aggr,
            "options_safe": options_safe, "crypto": crypto}
