"""The seven traders' rulebooks. Each turns the morning meeting's plan (if there is one) plus the scanner into
paper orders with a what, a why and a how-much. With no meeting that day, the rules run on autopilot."""
import datetime as dt
import json
import math

import pandas as pd

from . import core, data, settings
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
    traded = {x["symbol"] for x in core.read_trades() if x["trader"] == tid and x["ts"][:10] == ctx["date"]}
    out = []
    for row in ctx.get("intraday", {}).get("rows", []):
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
        why = (f"{s} gapped {row['gap_pct']:+.0f}% on {row['rel_vol']:.0f}x normal volume; buy a break above the "
               f"15-min high ${row['or_high']:.2f}, stop under the range low ${stop:.2f}, target {r['target_r']}R")
        if row.get("tabled"):
            why = "Tabled at the meeting. " + why
        out.append(order(a, "stock", s, qty, why, "meeting" if row.get("tabled") else "autopilot", "stop", entry, stop,
                         round(entry + r["target_r"] * risk, 2), meta={"small_cap": True}, valid_hours=6,
                         flat_by=r["flat_by"], no_entry_after=r["no_entry_after"]))
    return out


# ------------------------------------------------------------------ 2. swing trader
def swing(tid, a, ctx):
    r, t = R(tid), ctx["now_ny"]
    if not core.market_open_at(t) or t.time() < dt.time(9, 45):
        return []
    slots = r["max_positions"] - len(a["positions"]) - len(a["pending"])
    if slots <= 0 or not _once(a, "swing_pick", ctx["date"]):
        return []
    tnx = ctx.get("tnx_5d")
    rate_note = "" if tnx is None else (f" 10-yr yield {tnx:+.2f} pts in 5 days" +
                                        (" (rising: favour cash-rich leaders)" if tnx > 0.1 else " (falling: tailwind for growth)" if tnx < -0.1 else ""))
    picks = [("Tabled at the meeting", x) for x in ctx.get("tabled_rows", [])]
    picks += [("Insight from the 4-a-day reports", x) for x in ctx.get("report_rows", [])]
    picks += [("20-day breakout", x) for x in _scan(ctx, "breakout_20d")[:15]]
    picks += [("Pullback in an uptrend", x) for x in _scan(ctx, "pullback_uptrend")[:15]]
    order_ = ranked(tid, [x for _, x in picks])
    lab = {id(x): l for l, x in picks}
    picks = [(lab[id(x)], x) for x in order_]
    out, n_new = [], min(slots, r.get("new_per_day", 3))
    for label, x in picks:
        if len(out) >= n_new:
            break
        s = x["symbol"]
        if held(a, s) or not x.get("atr") or x["price"] < 5:
            continue
        e = data.next_earnings(s)
        if e and 0 <= (pd.Timestamp(e).date() - t.date()).days <= r["avoid_earnings_within_days"]:
            core.log("skip", trader=tid, symbol=s, reason=f"earnings {e} too close")
            continue
        stop = round(x["price"] - r["stop_atr"] * x["atr"], 2)
        qty = _size(ctx["equity"], buying_power(a), x["price"], x["price"] - stop, r["risk_pct"], r["max_position_pct"])
        if qty < 1:
            continue
        why = (f"{label}: {s} at ${x['price']:.2f}, {x.get('d5', 0) * 100:+.1f}% in 5 days, RSI {x.get('rsi', 0):.0f}, "
               f"volume {x.get('rel_vol', 0):.1f}x. Stop {r['stop_atr']} ATR, target {r['target_atr']} ATR, out in "
               f"{r['time_stop_days']} days if neither.{rate_note}")
        out.append(order(a, "stock", s, qty, why, "meeting" if label.startswith("Tabled") else "autopilot", "market",
                         None, stop, round(x["price"] + r["target_atr"] * x["atr"], 2),
                         time_stop=(t.date() + dt.timedelta(days=14)).isoformat()))
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
    for p in [p for p in a["positions"] if p["kind"] == "stock"]:
        i = data.info(p["symbol"])
        if (i.get("revenueGrowth") or 0) < 0:
            out.append({"close": p["id"], "why": f"Quarterly review: {p['symbol']} revenue now shrinking ({i.get('revenueGrowth'):+.1%})"})
    slots = r["max_positions"] - len(a["positions"]) - len(a["pending"])
    if slots <= 0:
        return out
    cfg = __import__("papertrade.scanner", fromlist=["x"]).yaml_cfg()
    pool = list(dict.fromkeys([x["symbol"] for x in ctx.get("tabled_rows", [])] + cfg["universe"]["growth"] +
                              cfg["universe"]["stocks"] + cfg["growth_screen"]["tsx"]))
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
        if g >= r["min_revenue_growth"] and m >= r["min_operating_margin"]:
            sm = settings.sector_mult(tid, i.get("sector"))
            if sm > 0:
                best.append(((g + m / 2 + y * 3 + (0.05 if y > 0 else 0)) * sm, s, last, s50, g, m, y, i.get("shortName", s)))
    best.sort(reverse=True)
    per = ctx["equity"] / r["max_positions"]
    for score, s, last, s50, g, m, y, name in best[:slots]:
        px = cad(s, last, ctx["fx"])
        qty = core.whole(min(per, buying_power(a)) / px)
        if qty < 1:
            continue
        why = (f"{name}: revenue {g:+.0%}, operating margin {m:.0%}, dividend {y:.1%}; bought on a pullback to its rising "
               f"50-day average ({last / s50 - 1:+.1%}). Held for the long term; reviewed each quarter after earnings.")
        out.append(order(a, "stock", s, qty, why, meta={"yahoo": s, "fx": s.endswith(".TO")}, valid_hours=8))
    return out


# ------------------------------------------------------------------ 4. futures & metals
def futures(tid, a, ctx):
    r, cs = R(tid), T[tid]["contracts"]
    if not _once(a, "fut_daily", ctx["date"]):
        return []
    a["pending"] = [o for o in a["pending"] if o["kind"] != "future"]  # yesterday's breakout orders expire
    d = {k: v.assign(**{c: v[c] * ctx["fx"] for c in ("open", "high", "low", "close")})
         for k, v in data.daily([c["yahoo"] for c in cs.values()], "1y").items()}
    out = []
    margin_used = sum(p.get("margin", 0) for p in a["positions"])
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
               f"{'high' if side == 'long' else 'low'} US${trig / ctx['fx']:,.2f}; stop {r['stop_atr']} ATR, target {r['target_atr']} ATR. "
               f"Notional C${n * trig * c['multiplier']:,.0f} on C${ctx['equity']:,.0f} equity.")
        out.append(order(a, "future", code, n, why, entry_type="stop", price=trig, stop=round(trig - sign * r["stop_atr"] * atr, 4),
                         target=round(trig + sign * r["target_atr"] * atr, 4), side=side,
                         meta={**c, "yahoo": c["yahoo"]}, valid_hours=24))
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
               f"Take profit at +{r['take_profit_pct']}%, cut at {r['stop_pct']}%. Max loss = premium paid.")
        o = None
        for otm in (r["otm_pct"], r["otm_pct"] * 2, r["otm_pct"] * 3.5):  # further out of the money until it fits the budget
            o = option_order(tid, a, ctx, s, right, "BUY_TO_OPEN", [r["dte_min"], r["dte_max"]], otm, why,
                             "meeting" if x in ctx.get("tabled_rows", []) else "autopilot")
            if o:
                break
        if o:
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
        if s["symbol"] in calls or s["qty"] < 100:
            continue
        why = (f"Wheel step 2: own {s['qty']:g} {s['symbol']} (cost ${s['entry']:.2f}); sell the {{exp}} ${{strike:g}} covered call "
               f"for ${{prem:.2f}} a share. Keep the premium; shares are sold at the strike if it closes above.")
        o = option_order(tid, a, ctx, s["symbol"], "C", "SELL_TO_OPEN", [r["dte_min"], r["dte_max"]], r["call_otm_pct"], why, "autopilot",
                         contracts=int(s["qty"] // 100))
        if o:
            out.append(o)
    if any(p["kind"] == "option" and p["meta"]["right"] == "P" for p in a["positions"]) or stocks:
        return out
    pool = [x["symbol"] for x in ctx.get("tabled_rows", []) if x["price"] <= r["max_strike"] * 1.1] + r["universe"]
    d = data.daily(pool, "1y")
    best = None
    for s in pool[:10]:
        df = d.get(s)
        if df is None or len(df) < 200:
            continue
        last = float(df["close"].iloc[-1])
        if last > r["max_strike"] / (1 - r["put_otm_pct"]) or last < float(df["close"].rolling(200).mean().iloc[-1]):
            continue
        exp = data.pick_expiry(s, r["dte_min"], r["dte_max"], t.date())
        c = data.pick_contract(s, exp, "P", last * (1 - r["put_otm_pct"])) if exp else None
        if not c or c["bid"] <= 0 or c["strike"] * 100 * ctx["fx"] > buying_power(a):
            continue
        yld = c["bid"] / c["strike"]
        if not best or yld > best[0]:
            best = (yld, s)
    if best:
        why = ("Wheel step 1: {underlying} is above its 200-day; sell the {exp} ${strike:g} cash-secured put for ${prem:.2f} "
               "(stock ${px:.2f}). ${strike:g} x 100 held in cash. If it closes below the strike we buy 100 shares there.")
        o = option_order(tid, a, ctx, best[1], "P", "SELL_TO_OPEN", [r["dte_min"], r["dte_max"]], r["put_otm_pct"],
                         why.replace("{underlying}", best[1]), "autopilot")
        if o:
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
    px = {c: float(df["close"].iloc[-1]) * ctx["fx"] for c, df in d.items()}
    out = []
    bucket_of = lambda p: p.get("meta", {}).get("bucket")

    def buy(c, usd, why, bucket, stop=None, take=None):
        if c not in px or usd < 10:
            return
        q = round(min(usd, buying_power(a)) / px[c], 8)
        if q <= 0:
            return
        out.append(order(a, "crypto", c, q, why, stop=round(px[c] * (1 + stop / 100), 8) if stop else None,
                         target=round(px[c] * (1 + take / 100), 8) if take else None,
                         meta={"bucket": bucket, "yahoo": c}, valid_hours=24))

    if _once(a, "crypto_core", ctx["date"]):
        for c, w in zip(b["core"]["coins"], b["core"]["split"]):
            have = sum(p["qty"] * px.get(c, 0) for p in a["positions"] if p["symbol"] == c and bucket_of(p) == "core")
            want = eq * b["core"]["pct"] / 100 * w
            if have < want * (1 - r["rebalance_drift"]) and not held(a, c):
                buy(c, want - have, f"Core bucket ({b['core']['pct']}%): hold {c.split('-')[0]} long term; top up to {w:.0%} of the core.", "core")
        growth_held = [p for p in a["positions"] if bucket_of(p) == "growth"]
        if not growth_held:
            top = sorted([c for c in b["growth"]["coins"] if c in m30], key=lambda c: -m30[c])[:3]
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
    if not any(bucket_of(p) == "meme" for p in a["positions"]) and _once(a, "crypto_meme", ctx["date"]):
        cand = sorted([c for c in b["meme"]["coins"] if c in mom], key=lambda c: -mom[c])
        if cand and mom[cand[0]] > 0:
            c = cand[0]
            buy(c, eq * b["meme"]["pct"] / 100, f"Meme bucket: {c.split('-')[0]} has the hottest 3-day run ({mom[c]:+.0%}). "
                f"Lottery ticket: stop {r['meme_stop_pct']}%, take +{r['meme_take_pct']}%.", "meme", r["meme_stop_pct"], r["meme_take_pct"])
    return out


RULEBOOK = {"day": day, "swing": swing, "long": long, "futures": futures, "options_aggr": options_aggr,
            "options_safe": options_safe, "crypto": crypto}
