"""Market data for the Paper Desk: free Yahoo data via yfinance (delayed, unofficial; can lag)."""
import datetime as dt
import warnings

warnings.filterwarnings("ignore")
import pandas as pd
import yfinance as yf

_chain_cache, _info_cache = {}, {}


def _frame(raw, sym, many):
    try:
        d = raw[sym] if many else raw
    except KeyError:
        return None
    d = d.dropna(how="all")
    if d.empty or "Close" not in d:
        return None
    d = d.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    idx = d.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    d.index = idx
    return d


def bars(symbols, interval="5m", period="5d"):
    """{symbol: DataFrame(open, high, low, close, volume)} indexed by naive UTC bar START time."""
    symbols = sorted(set(s for s in symbols if s))
    out = {}
    for i in range(0, len(symbols), 150):
        chunk = symbols[i:i + 150]
        try:
            raw = yf.download(chunk, period=period, interval=interval, auto_adjust=False, progress=False,
                              threads=True, group_by="ticker", prepost=False)
        except Exception:
            continue
        for s in chunk:
            f = _frame(raw, s, len(chunk) > 1 or isinstance(raw.columns, pd.MultiIndex))
            if f is not None and len(f):
                out[s] = f
    return out


def daily(symbols, period="1y"):
    return bars(symbols, interval="1d", period=period)


def last_price(sym):
    b = bars([sym], "5m", "2d").get(sym)
    if b is None or b.empty:
        b = daily([sym], "5d").get(sym)
    return float(b["close"].iloc[-1]) if b is not None and len(b) else None


def usdcad():
    p = last_price("CAD=X")
    return p or 1.40


def info(sym):
    if sym not in _info_cache:
        try:
            _info_cache[sym] = yf.Ticker(sym).info or {}
        except Exception:
            _info_cache[sym] = {}
    return _info_cache[sym]


def next_earnings(sym):
    try:
        e = yf.Ticker(sym).calendar.get("Earnings Date") or []
        return e[0] if e else None
    except Exception:
        return None


# ------------------------------------------------------------------ options
def expiries(sym):
    try:
        return list(yf.Ticker(sym).options)
    except Exception:
        return []


def pick_expiry(sym, dte_min, dte_max, today=None):
    today = today or dt.date.today()
    best = None
    for e in expiries(sym):
        d = (dt.date.fromisoformat(e) - today).days
        if dte_min <= d <= dte_max:
            return e
        if d > dte_max and best is None:
            best = e
    return best


def chain(sym, expiry):
    key = (sym, expiry)
    if key not in _chain_cache:
        try:
            c = yf.Ticker(sym).option_chain(expiry)
            _chain_cache[key] = (c.calls, c.puts)
        except Exception:
            _chain_cache[key] = (pd.DataFrame(), pd.DataFrame())
    return _chain_cache[key]


def pick_contract(sym, expiry, right, target_strike):
    calls, puts = chain(sym, expiry)
    df = calls if right == "C" else puts
    if df is None or df.empty:
        return None
    df = df[(df["bid"].fillna(0) > 0) | (df["lastPrice"].fillna(0) > 0)].copy()
    if df.empty:
        return None
    df["dist"] = (df["strike"] - target_strike).abs()
    r = df.sort_values("dist").iloc[0]
    return {"contract": r["contractSymbol"], "strike": float(r["strike"]), "bid": float(r.get("bid") or 0),
            "ask": float(r.get("ask") or 0), "last": float(r.get("lastPrice") or 0),
            "iv": float(r.get("impliedVolatility") or 0)}


def contract_quote(contract, underlying, expiry, right, strike):
    calls, puts = chain(underlying, expiry)
    df = calls if right == "C" else puts
    if df is not None and not df.empty:
        row = df[df["contractSymbol"] == contract]
        if len(row):
            r = row.iloc[0]
            return {"bid": float(r.get("bid") or 0), "ask": float(r.get("ask") or 0), "last": float(r.get("lastPrice") or 0)}
    return None
