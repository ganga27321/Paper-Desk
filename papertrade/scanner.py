"""Hybrid scanner for the Paper Desk: the whole US + Canadian market, with the morning meeting's tabled tickers first.

premarket  - FAST (about a minute): Yahoo's own screener filters the whole US + Canadian market on Yahoo's side
             (about 3,600 liquid stocks plus small caps, 250 per page, with price, volume, 50/200-day averages and
             52-week high), the lists are built from that snapshot, then price history is pulled only for the
             ~200 shortlisted names to confirm RSI, ATR and 20-day highs; writes state/scans/premarket.json
deep       - the old full download of every US listing (Nasdaq Trader directories) and TSX/TSXV listing (TSX
             company directory): 20-40 minutes, kept for a weekly sanity check (python -m papertrade deep)
intraday   - Yahoo's live gainers/actives screens + the premarket small-cap list + tabled tickers on 5-minute bars:
             gaps, relative volume, opening ranges; writes state/scans/intraday.json
A scan is a list of charts that match a rule, not a list of things to buy.
"""
import datetime as dt
import json
import re
import urllib.request

import numpy as np
import pandas as pd
import yfinance as yf

from . import core, data

SCANS = core.STATE / "scans"
UA = {"User-Agent": "Mozilla/5.0 (paper-desk scanner)"}


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _fallback_universe():
    return sorted(set(core.CFG.get("fallback_universe", [])))


def universe(max_age_days=7):
    """[(symbol, name, market)]: US common stocks and ADRs + TSX (.TO) and TSXV (.V). Cached for a week."""
    p = SCANS / "universe.json"
    if p.exists():
        u = json.loads(p.read_text())
        if (dt.date.today() - dt.date.fromisoformat(u["built"])).days < max_age_days and len(u["symbols"]) > 500:
            return u["symbols"]
    syms, notes = [], []
    try:
        texts = {f: _get(core.skill.NASDAQ_URL + f) for f in core.skill.NASDAQ_FILES}
        kept, counts, _ = core.skill.build_universe(texts)
        syms += [(s.replace(".", "-"), n, "US") for s, n in kept]
        notes.append(f"US {len(kept)}")
    except Exception as e:
        notes.append(f"US directory failed: {e}")
    for board, suffix in (("tsx", ".TO"), ("tsxv", ".V")):
        try:
            doc = json.loads(_get(f"https://www.tsx.com/json/company-directory/search/{board}/%5E*"))
            n0 = len(syms)
            for c in doc.get("results", []):
                for ins in c.get("instruments", []) or [{"symbol": c.get("symbol"), "name": c.get("name")}]:
                    s = (ins.get("symbol") or "").strip()
                    if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z]{1,2})?", s) and not re.search(r"\.(DB|WT|RT|PR|U)", s):
                        syms.append((s.replace(".", "-") + suffix, ins.get("name") or c.get("name", ""), "CA"))
            notes.append(f"{board.upper()} {len(syms) - n0}")
        except Exception as e:
            notes.append(f"{board.upper()} directory failed: {e}")
    if len(syms) < 500:
        syms = [(s, "", "CA" if s.endswith(".TO") else "US") for s in _fallback_universe()]
        notes.append("using the fallback universe in traders.yaml")
    SCANS.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"built": dt.date.today().isoformat(), "notes": notes, "symbols": syms}))
    return syms


def _rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n).mean()
    return 100 - 100 / (1 + up / dn)


def metrics(df):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    if len(c) < 25 or c.iloc[-1] <= 0:
        return None
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    last = float(c.iloc[-1])
    av20 = float(v.iloc[-21:-1].mean() or 0)
    sma = lambda n: float(c.rolling(n).mean().iloc[-1]) if len(c) >= n else np.nan
    return {"price": round(last, 4), "d1": float(last / c.iloc[-2] - 1), "d5": float(last / c.iloc[-6] - 1),
            "d20": float(last / c.iloc[-21] - 1), "atr_pct": float(tr.rolling(14).mean().iloc[-1] / last),
            "rel_vol": float(v.iloc[-1] / av20) if av20 else 0.0, "dollar_vol": float(av20 * last),
            "sma20": sma(20), "sma50": sma(50), "sma200": sma(200), "rsi": float(_rsi(c).iloc[-1]),
            "hi20": float(c.iloc[-21:-1].max()), "lo20": float(c.iloc[-21:-1].min()), "atr": float(tr.rolling(14).mean().iloc[-1])}


# ------------------------------------------------------------------ fast scan via Yahoo's screener
def _screen_pages(query, cap=4000, sort="dayvolume"):
    from yfinance import EquityQuery  # noqa: F401  (import check)
    out, off = {}, 0
    while off < cap:
        try:
            r = yf.screen(query, size=250, offset=off, sortField=sort, sortAsc=False)
        except Exception as e:
            core.log("scan", warning=f"screener page {off} failed: {e!r}"[:200])
            break
        qs = r.get("quotes", [])
        for x in qs:
            s = x.get("symbol", "")
            if s and (("." not in s) or s.endswith((".TO", ".V"))):
                out[s] = x
        off += 250
        if len(qs) < 250 or off >= (r.get("total") or 0):
            break
    return out


def snapshot():
    """One pass over the market on Yahoo's side: liquid US + Canadian stocks, plus small caps that are moving."""
    from yfinance import EquityQuery as Q
    region = Q("is-in", ["region", "us", "ca"])
    liquid = Q("and", [region, Q("gt", ["intradaymarketcap", 2e8]), Q("gt", ["dayvolume", 2e5]), Q("gt", ["intradayprice", 1])])
    small = Q("and", [region, Q("btwn", ["intradayprice", 1, 20]), Q("gt", ["dayvolume", 3e5])])
    quotes = _screen_pages(liquid)
    quotes.update(_screen_pages(small, cap=1500, sort="percentchange"))
    return quotes


def _row(s, x):
    g = lambda k, d=None: x.get(k) if x.get(k) is not None else d
    price = g("regularMarketPrice", 0) or 0
    avg = g("averageDailyVolume3Month", 0) or g("averageDailyVolume10Day", 0) or 0
    return {"symbol": s, "name": g("shortName", ""), "price": price, "d1": (g("regularMarketChangePercent", 0) or 0) / 100,
            "rel_vol": (g("regularMarketVolume", 0) or 0) / avg if avg else 0.0, "dollar_vol": avg * price,
            "sma50": g("fiftyDayAverage"), "sma200": g("twoHundredDayAverage"), "hi52": g("fiftyTwoWeekHigh"),
            "mcap": g("marketCap"), "earnings_ts": g("earningsTimestampStart"), "us": not s.endswith((".TO", ".V"))}


def premarket(tabled=()):
    """Fast whole-market scan (see the module note). Tabled tickers are always included and reported."""
    t0 = core.utcnow()
    quotes = snapshot()
    if len(quotes) < 300:  # the screener failed: fall back to the full download
        core.log("scan", warning=f"screener gave {len(quotes)} rows; running the deep scan")
        return deep(tabled)
    df = pd.DataFrame([_row(s, x) for s, x in quotes.items()]).set_index("symbol")
    num = df.apply(pd.to_numeric, errors="coerce")
    short = {}
    short["small_cap_movers"] = num[num.price.between(1, 20) & (num.dollar_vol >= 2e6) & ((num.d1.abs() >= 0.04) | (num.rel_vol >= 2))]
    liquid = num[num.dollar_vol >= 2e7]
    short["breakout_20d"] = liquid[(liquid.price >= liquid.hi52 * 0.9) & (liquid.price > liquid.sma50)]
    short["pullback_uptrend"] = liquid[(liquid.price > liquid.sma200) & (liquid.sma50 > liquid.sma200) &
                                       ((liquid.price / liquid.sma50 - 1).between(-0.06, 0.01))]
    big = num[(num.dollar_vol >= 2e8) & (num.price >= 15) & df.us]
    short["momentum_options"] = big
    short["oversold"] = liquid[(liquid.price / liquid.sma50 - 1 < -0.10) & (liquid.price > liquid.sma200 * 0.9)]
    pick = {"small_cap_movers": lambda d: d.rel_vol * 2 + d.d1.abs() * 20, "breakout_20d": lambda d: d.rel_vol + d.price / d.hi52,
            "pullback_uptrend": lambda d: -(d.price / d.sma50 - 1).abs(), "momentum_options": lambda d: d.d1.abs() * 10 + d.rel_vol,
            "oversold": lambda d: -(d.price / d.sma50)}
    shortlist = set(tabled)
    for k, d in short.items():
        short[k] = d.assign(score=pick[k](d)).sort_values("score", ascending=False).head(60)
        shortlist |= set(short[k].index)
    hist = data.bars(sorted(shortlist), interval="1d", period="1y")   # history only for the shortlist
    met = {s: m for s, df_ in hist.items() if (m := metrics(df_))}
    out = {"asof": core.utcnow().isoformat() + "Z", "method": "fast (Yahoo screener + shortlist history)",
           "scanned": len(quotes), "with_data": len(met), "seconds": (core.utcnow() - t0).seconds, "tabled": {}, "lists": {}}
    for t in tabled:
        if t in met:
            out["tabled"][t] = met[t]
    rules = {"small_cap_movers": lambda m: m["atr_pct"] >= 0.05, "breakout_20d": lambda m: m["price"] > m["hi20"],
             "pullback_uptrend": lambda m: 35 <= m["rsi"] <= 55, "momentum_options": lambda m: True,
             "oversold": lambda m: m["rsi"] < 32}
    for k, d in short.items():
        rows = []
        for s, r in d.iterrows():
            m = met.get(s)
            if m and rules[k](m):
                rows.append({"symbol": s, **{a: round(float(b), 4) for a, b in m.items()}, "score": round(float(r["score"]), 4),
                             "name": quotes[s].get("shortName", "")})
        out["lists"][k] = rows[:40]
    return _write("premarket.json", out)


def deep(tabled=()):
    """Full-market daily scan by downloading every listing (slow; weekly sanity check). Tabled tickers first."""
    syms = universe()
    order = list(dict.fromkeys(list(tabled) + [s for s, _, _ in syms]))
    rows = {}
    for i in range(0, len(order), 200):
        got = data.bars(order[i:i + 200], interval="1d", period="1y" if i == 0 else "6mo")
        for s, df in got.items():
            m = metrics(df)
            if m:
                rows[s] = m
    df = pd.DataFrame(rows).T
    out = {"asof": core.utcnow().isoformat() + "Z", "scanned": len(order), "with_data": len(df), "tabled": {}, "lists": {}}
    for t in tabled:
        if t in rows:
            out["tabled"][t] = rows[t]
    if df.empty:
        return _write("premarket.json", out)
    num = df.apply(pd.to_numeric, errors="coerce")
    small = num[(num.price.between(1, 20)) & (num.atr_pct >= 0.06) & (num.dollar_vol >= 2e6)]
    out["lists"]["small_cap_movers"] = _top(small.assign(score=small.rel_vol * 2 + small.d1.abs() * 20 + small.atr_pct * 10), 60)
    liquid = num[num.dollar_vol >= 2e7]
    brk = liquid[(liquid.price > liquid.hi20) & (liquid.price > liquid.sma50)]
    out["lists"]["breakout_20d"] = _top(brk.assign(score=brk.rel_vol + brk.d20 * 5), 40)
    pull = liquid[(liquid.price > liquid.sma200) & (liquid.sma50 > liquid.sma200) & (liquid.rsi.between(35, 52)) &
                  ((liquid.price / liquid.sma20 - 1).abs() < 0.03)]
    out["lists"]["pullback_uptrend"] = _top(pull.assign(score=-pull.rsi), 40)
    big = num[(num.dollar_vol >= 2e8) & (num.price >= 15)]
    big = big[[not s.endswith((".TO", ".V")) for s in big.index]]
    out["lists"]["momentum_options"] = _top(big.assign(score=big.d5.abs() * 10 + big.rel_vol), 30)
    oversold = liquid[(liquid.rsi < 30) & (liquid.price > liquid.sma200 * 0.9)]
    out["lists"]["oversold"] = _top(oversold.assign(score=-oversold.rsi), 30)
    return _write("premarket.json", out)


def _top(df, n):
    df = df.sort_values("score", ascending=False).head(n)
    return [{"symbol": s, **{k: (None if pd.isna(v) else round(float(v), 4)) for k, v in r.items()}} for s, r in df.iterrows()]


def _write(name, obj):
    SCANS.mkdir(parents=True, exist_ok=True)
    (SCANS / name).write_text(json.dumps(obj, indent=1, default=str))
    return obj


def _bar_date(ix):
    """The trading date of a daily bar: its own date when naive (Yahoo's daily label), else its New York date."""
    t = ix.to_pydatetime()
    return t.date() if t.tzinfo is None else core.ny(t.astimezone(dt.timezone.utc).replace(tzinfo=None)).date()


def intraday(tabled=()):
    """Live small-cap gappers on 5-minute bars: gap vs yesterday, volume so far, opening range."""
    cands = set(tabled)
    for q in ("small_cap_gainers", "day_gainers", "most_actives", "aggressive_small_caps"):
        try:
            r = yf.screen(q, count=100)
            cands |= {x["symbol"] for x in r.get("quotes", []) if x.get("symbol")}
        except Exception:
            pass
    try:  # custom gapper screen on Yahoo's side: small caps up 5%+ on volume, US + Canada
        from yfinance import EquityQuery as Q
        gap = Q("and", [Q("is-in", ["region", "us", "ca"]), Q("btwn", ["intradayprice", 1, 20]),
                        Q("gt", ["percentchange", 5]), Q("gt", ["dayvolume", 3e5])])
        cands |= set(_screen_pages(gap, cap=250, sort="percentchange"))
    except Exception:
        pass
    pm = SCANS / "premarket.json"
    if pm.exists():
        cands |= {x["symbol"] for x in json.loads(pm.read_text()).get("lists", {}).get("small_cap_movers", [])[:60]}
    cands = sorted(cands)
    daily = data.bars(cands, "1d", "1mo")
    five = data.bars(cands, "5m", "1d")
    rows = []
    today = core.ny().date()
    for s in cands:
        d, f = daily.get(s), five.get(s)
        if d is None or f is None or len(d) < 5 or f.empty:
            continue
        if core.ny(f.index[-1].to_pydatetime()).date() != today:
            continue
        # daily bars are labelled by trading date (naive midnight), not a UTC instant: core.ny() would turn today's
        # bar into "yesterday 19:00" and make yesterday's close the live price (every gap read ~0 vs the open)
        prev = float(d["close"].iloc[-2]) if _bar_date(d.index[-1]) == today else float(d["close"].iloc[-1])
        first = f.iloc[0]
        orb = f.iloc[:3]
        vol_today = float(f["volume"].sum())
        avg = float(d["volume"].iloc[-21:-1].mean() or 1)
        mins = max(5, 5 * len(f))
        tp = (f["high"] + f["low"] + f["close"]) / 3
        vwap = float((tp * f["volume"]).sum() / max(float(f["volume"].sum()), 1.0))
        rows.append({"symbol": s, "price": float(f["close"].iloc[-1]), "prev_close": prev,
                     "gap_pct": 100 * (float(first["open"]) / prev - 1), "chg_pct": 100 * (float(f["close"].iloc[-1]) / prev - 1),
                     "rel_vol": vol_today / (avg * mins / 390), "or_high": float(orb["high"].max()), "or_low": float(orb["low"].min()), "vwap": vwap,
                     "bars": len(f), "tabled": s in tabled})
    rows.sort(key=lambda r: (-r["tabled"], -r["rel_vol"] * abs(r["gap_pct"])))
    return _write("intraday.json", {"asof": core.utcnow().isoformat() + "Z", "candidates": len(cands), "rows": rows[:80]})
