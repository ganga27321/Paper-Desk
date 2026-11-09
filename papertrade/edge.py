"""Shared tools every trader can read; each trader reacts to them in its own way (see strategies.py).

- regime():        market mood from SPY's trend and the VIX -> risk_on / neutral / risk_off
- size_mult():     how much a trader sizes down in that mood (rules: regime_size)
- earnings_view(): the last report's gap and whether it held, plus the next report date
- headlines():     fresh news headlines for a ticker (last N hours)
- fear_greed():    the crypto Fear & Greed index (alternative.me)
- cooling():       True while a trader is barred from a symbol after a stop-out (rules: cooldown_days)
All network calls fail soft: a missing input never stops a trader, it just skips that rule.
"""
import datetime as dt
import json
import urllib.request

import pandas as pd
import yfinance as yf

from . import core, data, settings

_cache = {}


def regime():
    """{"mode", "vix", "spy_vs_50d", "spy_vs_200d", "why"} - computed once per run."""
    if "regime" in _cache:
        return _cache["regime"]
    out = {"mode": "neutral", "vix": None, "spy_vs_50d": None, "spy_vs_200d": None, "why": "no data"}
    try:
        d = data.daily(["SPY", "^VIX"], "1y")
        spy, vix = d["SPY"]["close"], float(d["^VIX"]["close"].iloc[-1])
        last = float(spy.iloc[-1])
        v50, v200 = last / float(spy.rolling(50).mean().iloc[-1]) - 1, last / float(spy.rolling(200).mean().iloc[-1]) - 1
        if vix >= 28 or (v200 < 0 and v50 < 0):
            mode = "risk_off"
        elif vix <= 18 and v50 > 0 and v200 > 0:
            mode = "risk_on"
        else:
            mode = "neutral"
        out = {"mode": mode, "vix": round(vix, 1), "spy_vs_50d": round(v50, 4), "spy_vs_200d": round(v200, 4),
               "why": f"VIX {vix:.0f}, S&P {v50:+.1%} vs its 50-day, {v200:+.1%} vs its 200-day"}
    except Exception:
        pass
    _cache["regime"] = out
    return out


def size_mult(tid, reg=None):
    reg = reg or regime()
    return float(settings.rules(tid).get("regime_size", {}).get(reg["mode"], 1.0))


def earnings_view(sym, daily_df=None):
    """Last report: date, gap %, whether the stock held above the gap-day midpoint since, days since; next report date.
    Gap is read from prices (open after the report vs the close before), so it works for any listed stock."""
    key = ("earn", sym)
    if key in _cache:
        return _cache[key]
    out = {}
    try:
        t = yf.Ticker(sym)
        ed = t.get_earnings_dates(limit=8)
        if ed is not None and len(ed):
            idx = pd.to_datetime(ed.index).tz_localize(None) if ed.index.tz is not None else pd.to_datetime(ed.index)
            now = pd.Timestamp(core.utcnow())
            past = [(i, r) for i, r in zip(idx, ed.to_dict("records")) if i <= now]
            fut = [i for i in idx if i > now]
            out["next"] = min(fut).date().isoformat() if fut else None
            if past:
                when, rec = max(past, key=lambda x: x[0])
                out["last"] = when.date().isoformat()
                out["surprise_pct"] = rec.get("Surprise(%)")
                df = daily_df if daily_df is not None else data.daily([sym], "3mo").get(sym)
                if df is not None and len(df) > 5:
                    # report after the close -> reaction day is the next session; before the open -> same day
                    react = df[df.index.normalize() > when.normalize()] if when.hour >= 12 else df[df.index.normalize() >= when.normalize()]
                    before = df[df.index.normalize() < react.index[0].normalize()] if len(react) else None
                    if len(react) and before is not None and len(before):
                        g = react.iloc[0]
                        prev = float(before["close"].iloc[-1])
                        mid = (float(g["high"]) + float(g["low"])) / 2
                        out.update({"gap_pct": round(100 * (float(g["open"]) / prev - 1), 2),
                                    "day_low": float(g["low"]), "day_high": float(g["high"]), "mid": mid,
                                    "held": bool((react["close"] >= mid).all()),
                                    "sessions_since": int(len(react) - 1), "last_close": float(df["close"].iloc[-1])})
    except Exception:
        pass
    _cache[key] = out
    return out


def earnings_within(sym, days, today=None):
    nxt = earnings_view(sym).get("next") or (str(data.next_earnings(sym))[:10] if data.next_earnings(sym) else None)
    if not nxt:
        return False
    today = today or core.ny().date()
    return 0 <= (dt.date.fromisoformat(nxt[:10]) - today).days <= days


def headlines(sym, hours=48):
    """Fresh headlines, [] if none, or None if the news source itself is unreachable (rules then don't block on news)."""
    key = ("news", sym, hours)
    if key in _cache:
        return _cache[key]
    if _cache.get("news_down"):
        return None
    out = []
    try:
        cut = core.utcnow() - dt.timedelta(hours=hours)
        for n in (yf.Ticker(sym).news or [])[:15]:
            c = n.get("content", n)
            ts = c.get("pubDate") or c.get("providerPublishTime")
            when = (dt.datetime.fromisoformat(ts.replace("Z", "")) if isinstance(ts, str)
                    else dt.datetime.utcfromtimestamp(ts) if ts else None)
            if when and when >= cut and c.get("title"):
                out.append(c["title"])
    except Exception:
        _cache["news_down"] = True
        return None
    _cache[key] = out
    return out


def fear_greed():
    """Crypto Fear & Greed (0 = extreme fear, 100 = extreme greed) or None. Every live reading is saved to
    state/fear_greed.json; a run that can't reach alternative.me (an outage, or a network that blocks it, like a cloud
    meeting session) uses the last saved reading while it is under 36 hours old."""
    if "fg" in _cache:
        return _cache["fg"]
    v, src, at = None, None, None
    f = core.STATE / "fear_greed.json"
    try:
        with urllib.request.urlopen("https://api.alternative.me/fng/?limit=1", timeout=15) as r:
            v = int(json.loads(r.read())["data"][0]["value"])
        src, at = "live", core.utcnow().isoformat() + "Z"
        try:
            f.write_text(json.dumps({"value": v, "at": at}))
        except OSError:
            pass
    except Exception:
        try:
            saved = json.loads(f.read_text())
            if core.utcnow() - dt.datetime.fromisoformat(saved["at"].rstrip("Z")) <= dt.timedelta(hours=36):
                v, src, at = int(saved["value"]), "saved", saved["at"]
        except Exception:
            pass
    _cache["fg"], _cache["fg_info"] = v, {"value": v, "source": src, "at": at}
    return v


def fear_greed_info():
    """The Fear & Greed reading with where it came from (live, or the last saved one) and when, for the meeting."""
    fear_greed()
    return _cache.get("fg_info") or {"value": _cache.get("fg"), "source": None, "at": None}


def cooling(tid, sym, today=None):
    """True if this trader was stopped out of this symbol within its cooldown window."""
    days = settings.rules(tid).get("cooldown_days", 0)
    if not days:
        return False
    today = today or core.ny().date()
    for t in reversed(core.read_trades()):
        if t["trader"] == tid and t["symbol"] == sym and "stop" in (t.get("why") or "") and t.get("pnl") not in ("", None):
            return (today - dt.date.fromisoformat(t["ts"][:10])).days < days
    return False
