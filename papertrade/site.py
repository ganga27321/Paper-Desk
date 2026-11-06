"""Builds the Paper Desk dashboard (static site) into papertrade/_site: the HTML/CSS/JS from papertrade/web plus
data.json computed from the desk's state, and the weekly PDFs."""
import csv
import glob
import json
import shutil
from pathlib import Path

from . import core, settings

WEB = core.ROOT / "web"
OUT = core.ROOT / "_site"


def _equity():
    p = core.STATE / "equity.csv"
    series = {}
    if p.exists():
        with p.open(newline="") as f:
            for r in csv.DictReader(f):
                series.setdefault(r["trader"], []).append([r["ts"], float(r["equity"])])
    for tid, s in series.items():  # thin to at most one point per 30 minutes, keep the last
        out, last = [], ""
        for ts, v in s:
            k = ts[:15]
            if out and k[:13] == last[:13] and int(k[14]) // 3 == int(last[14]) // 3:
                out[-1] = [ts, v]
            else:
                out.append([ts, v])
            last = k
        series[tid] = out
    return series


def _stats(tid, trades, eq, start):
    closes = [t for t in trades if t["pnl"] not in ("", None)]
    pnl = [float(t["pnl"]) for t in closes]
    wins = [p for p in pnl if p > 0]
    vals = [v for _, v in eq] or [start]
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"trades": len(trades), "closed": len(closes), "win_rate": (len(wins) / len(closes)) if closes else None,
            "realized": sum(pnl), "best": max(pnl) if pnl else None, "worst": min(pnl) if pnl else None,
            "max_drawdown": mdd, "fees": sum(float(t["fee"] or 0) for t in trades)}


def _clean(x):
    """Browsers can't parse NaN/Infinity in JSON (e.g. a 200-day average for a stock with less history): send null."""
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def build():
    OUT.mkdir(exist_ok=True)
    for f in WEB.iterdir():
        if f.is_file():
            shutil.copy2(f, OUT / f.name)
    acc = core.load_accounts()
    trades = core.read_trades()
    eq = _equity()
    traders = []
    hist = settings.history()
    ny = core.ny()
    week = f"{ny.isocalendar()[0]}-W{ny.isocalendar()[1]:02d}"
    bp = core.STATE / "bonuses.json"
    bonuses = json.loads(bp.read_text()) if bp.exists() else {}
    sx = settings.export()
    for tid, t in core.CFG["traders"].items():
        a = acc[tid]
        mine = [r for r in trades if r["trader"] == tid]
        series = eq.get(tid, [])
        equity = series[-1][1] if series else a["cash"]
        put_in = t["capital"] + sum(d["amount"] for d in a.get("deposits", []))
        cur = sx["traders"][tid]
        prev = {"scales": dict(cur["scales"]), "sectors": dict(cur["sectors"])}  # undo this week's changes
        for h in reversed([h for h in hist if h["trader"] == tid and h["week"] == week]):
            if h["knob"].startswith("sector:"):
                prev["sectors"][h["knob"][7:]] = h["old"]
            else:
                prev["scales"][h["knob"]] = h["old"]
        traders.append({
            "id": tid, "name": t["name"], "title": t["title"], "style": t["style"], "tfsa": t["tfsa"],
            "tfsa_mirror": t.get("tfsa_mirror"), "account": t["account"], "capital": t["capital"],
            "cash": a["cash"], "equity": equity, "return": equity / put_in - 1, "series": series, "put_in": put_in,
            "deposits": a.get("deposits", []), "scales": cur, "scales_prev": prev,
            "trophies": sum(1 for b in bonuses.values() if b["winner"] == tid),
            "positions": a["positions"], "pending": a["pending"], "trades": mine[::-1],
            "week_trades": a["week"]["trades"], "rules": t.get("rules", {}), "stats": _stats(tid, mine, series, t["capital"])})
    plans = sorted(glob.glob(str(core.STATE / "plans" / "*.json")))
    scans = {}
    for name in ("premarket", "intraday"):
        p = core.STATE / "scans" / f"{name}.json"
        if p.exists():
            scans[name] = json.loads(p.read_text())
    pdfs = sorted(glob.glob(str(core.STATE / "weekly" / "*.pdf")))
    (OUT / "weekly").mkdir(exist_ok=True)
    for p in pdfs:
        shutil.copy2(p, OUT / "weekly" / Path(p).name)
    log = []
    lp = core.STATE / "log.jsonl"
    if lp.exists():
        log = [json.loads(x) for x in lp.read_text().splitlines()[-150:]][::-1]
    doc = {"updated": core.utcnow().isoformat() + "Z", "start_date": core.CFG["start_date"], "traders": traders,
           "currency": core.CFG["base_currency"], "repo": core.CFG.get("repo", ""), "week": week,
           "scales_meta": {k: v for k, v in sx.items() if k != "traders"}, "scale_history": hist[::-1],
           "bonuses": bonuses, "weekly_bonus": core.CFG.get("weekly_bonus", 200),
           "reviews": [json.loads(open(r).read()) for r in sorted(glob.glob(str(core.STATE / "reviews" / "*.json")))[-8:]][::-1],
           "plans": [json.loads(open(p).read()) for p in plans[-10:]][::-1], "scans": scans,
           "weekly": [Path(p).name for p in pdfs][::-1], "log": log}
    (OUT / "data.json").write_text(json.dumps(_clean(doc), default=str, allow_nan=False))
    return str(OUT)
