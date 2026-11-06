"""The Paper Desk core: config, time, state, ledger, fees, TFSA checks. Paper money only."""
import csv
import datetime as dt
import importlib.util
import json
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
STATE = ROOT / "state"           # checked out from the paper-data branch in workflows
CFG = yaml.safe_load((ROOT / "traders.yaml").read_text(encoding="utf-8"))
TRADE_FIELDS = ["ts", "trader", "action", "kind", "symbol", "qty", "price", "fee", "value", "pnl",
                "pct_of_equity", "why", "source", "tfsa_legal"]

FEED = STATE / "feed.json"      # market-only summary of the council and the 4-a-day reports (no personal data)

# NYSE calendar and the US universe builder (papertrade/nyse.py)
_spec = importlib.util.spec_from_file_location("skill_scan", ROOT / "nyse.py")
skill = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(skill)


# ------------------------------------------------------------------ time (New York, never the machine's zone)
def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def ny(utc=None):
    return skill.to_eastern(utc or utcnow())


def ny_from_ts(ts):
    return skill.ts_to_eastern(ts)


def session(d):
    return skill.session(d)


def market_open_at(t_ny):
    """True if a New York time is inside the regular session (TFSA traders trade only then)."""
    s = session(t_ny.date())
    return bool(s) and s[0] <= t_ny.time() < s[1]


def hhmm(text):
    h, m = text.split(":")
    return dt.time(int(h), int(m))


# ------------------------------------------------------------------ state
def _load(name, default):
    p = STATE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _save(name, obj):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / name).write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")


def load_accounts():
    acc = _load("accounts.json", {})
    if acc and acc.get("_meta", {}).get("currency") != CFG["base_currency"]:
        # the desk changed currency: archive the old books once and start everyone fresh
        arch = STATE / f"archive-{acc.get('_meta', {}).get('currency', 'USD').lower()}"
        arch.mkdir(parents=True, exist_ok=True)
        for f in ("accounts.json", "trades.csv", "equity.csv", "log.jsonl"):
            if (STATE / f).exists():
                (STATE / f).rename(arch / f)
        acc = {}
    acc["_meta"] = {"currency": CFG["base_currency"]}
    for tid, t in CFG["traders"].items():
        acc.setdefault(tid, {"cash": float(t["capital"]), "positions": [], "pending": [], "last_bar": {},
                             "day": {"date": "", "trades": 0}, "week": {"key": "", "trades": 0}, "realized": 0.0,
                             "seq": 0, "deposits": []})
        acc[tid].setdefault("deposits", [])
    return acc


def save_accounts(acc):
    _save("accounts.json", acc)


def new_id(a, prefix):
    a["seq"] = a.get("seq", 0) + 1
    return f"{prefix}{a['seq']}"


def append_trade(row):
    STATE.mkdir(parents=True, exist_ok=True)
    p = STATE / "trades.csv"
    new = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in TRADE_FIELDS})


def read_trades():
    p = STATE / "trades.csv"
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_equity(ts, rows):
    STATE.mkdir(parents=True, exist_ok=True)
    p = STATE / "equity.csv"
    new = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "trader", "cash", "positions", "equity"])
        for tid, cash, pos, eq in rows:
            w.writerow([ts, tid, round(cash, 2), round(pos, 2), round(eq, 2)])


def log(event, **kw):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": utcnow().isoformat() + "Z", "event": event, **kw}, default=str) + "\n")


def plan_for(date):
    p = STATE / "plans" / f"{date}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ------------------------------------------------------------------ fees and sizing
def fee(kind, qty, price, small_cap=False):
    f = CFG["fees"]
    if kind == "stock":
        return f["stock"] + abs(qty * price) * (f["slippage_small_cap_pct"] if small_cap else f["slippage_pct"])
    if kind == "option":
        return f["option_contract"] * abs(qty)
    if kind == "future":
        return f["future_contract"] * abs(qty)
    if kind == "crypto":
        return abs(qty * price) * f["crypto_pct"]
    return 0.0


def whole(x):
    return int(math.floor(x + 1e-9))


def tfsa_check(trader_id, order):
    """None if the order is allowed for this trader, else the reason. Sandbox traders pass but are labelled."""
    t = CFG["traders"][trader_id]
    if not t["tfsa"]:
        return None
    if order.get("side") == "short":
        return "no short selling in a TFSA"
    if order["kind"] == "future":
        return "futures are not allowed in a TFSA"
    if order["kind"] == "crypto":
        return "crypto coins are not allowed in a TFSA (spot crypto ETFs are)"
    if order["kind"] == "option" and order.get("action") == "SELL_TO_OPEN":
        if order.get("right") == "C" and not order.get("covered"):
            return "naked calls are not allowed in a TFSA (covered calls are)"
        if order.get("right") == "P" and not order.get("cash_secured"):
            return "naked puts are not allowed in a TFSA (cash-secured puts are)"
    if order["kind"] == "stock" and abs(order.get("qty", 0) - whole(order.get("qty", 0))) > 1e-9:
        return "whole shares only"
    return None
