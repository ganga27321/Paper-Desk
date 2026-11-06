"""Style scales: each trader's 0-10 dials, how they turn into rules, and the history of every change.

state/settings.json          {trader: {"scales": {...}, "sectors": {...}}}   (only what differs from traders.yaml)
state/settings_history.json  [{ts, week, trader, knob, old, new, why, by}]
"""
import json

from . import core

KNOBS = [("aggression", "Aggression", "risk per trade and position size"),
         ("frequency", "Trade frequency", "how many trades or positions at once"),
         ("holding", "Holding period", "how long a trade is given"),
         ("deployment", "Cash deployed", "how much of the account can be in the market"),
         ("stop_width", "Stop width", "room before a loser is cut"),
         ("target", "Profit target", "how far a winner runs")]
CAPS = {"risk_pct": (0.25, 60), "max_position_pct": (5, 100), "max_trades_day": (1, 12), "target_r": (0.5, 6),
        "max_positions": (1, 12), "new_per_day": (1, 6), "trades_week_goal": (1, 40), "time_stop_days": (1, 60),
        "stop_atr": (0.5, 6), "target_atr": (0.5, 12), "buy_vs_50d_max": (0, 0.15), "max_contracts": (1, 8),
        "dte_min": (1, 120), "dte_max": (3, 180), "stop_pct": (-95, -5), "take_profit_pct": (5, 500),
        "put_otm_pct": (0.01, 0.25), "meme_stop_pct": (-80, -3), "news_stop_pct": (-60, -2), "meme_take_pct": (5, 400),
        "news_take_pct": (3, 200)}
INTS = {"max_trades_day", "max_positions", "new_per_day", "trades_week_goal", "time_stop_days", "max_contracts", "dte_min", "dte_max"}
# a higher aggression means a CLOSER strike for the wheel (more premium, more assignment risk)
INVERSE = {("options_safe", "put_otm_pct")}


def _load(name, default):
    p = core.STATE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _save(name, obj):
    core.STATE.mkdir(parents=True, exist_ok=True)
    (core.STATE / name).write_text(json.dumps(obj, indent=1), encoding="utf-8")


def current(tid):
    t = core.CFG["traders"][tid]
    o = _load("settings.json", {}).get(tid, {})
    sectors = {s: 5 for s in core.CFG["sector_list"]}
    sectors.update(o.get("sectors", {}))
    return {"scales": {**t["scales"], **o.get("scales", {})}, "sectors": sectors}


def factor(k, k0):
    return 2 ** ((k - k0) / 2.5)


def rules(tid):
    """The trader's rules after the scales are applied."""
    t = core.CFG["traders"][tid]
    base = dict(t.get("rules", {}))
    cur = current(tid)["scales"]
    for knob, keys in (t.get("scale_map") or {}).items():
        f = factor(cur[knob], t["scales"][knob])
        for key in keys:
            if key not in base or not isinstance(base[key], (int, float)):
                continue
            v = base[key] / f if (tid, key) in INVERSE else base[key] * f
            lo, hi = CAPS.get(key, (-1e9, 1e9))
            v = min(max(v, lo), hi)
            base[key] = int(round(v)) if key in INTS else round(v, 4)
    if base.get("dte_max") and base.get("dte_min") and base["dte_max"] <= base["dte_min"]:
        base["dte_max"] = base["dte_min"] + 7
    base["deploy_frac"] = min(1.0, 0.2 + 0.08 * cur["deployment"])
    return base


def sector_mult(tid, sector):
    """Ranking multiplier for an industry: 0 -> excluded, 5 -> 1x, 10 -> 2x."""
    if not sector:
        return 1.0
    w = current(tid)["sectors"].get(sector, 5)
    return 0.0 if w <= 0 else 2 ** ((w - 5) / 5)


def apply(tid, changes, why, by):
    """Change scales/sectors for one trader; records old -> new in the history."""
    allset = _load("settings.json", {})
    mine = allset.setdefault(tid, {})
    cur = current(tid)
    hist = _load("settings_history.json", [])
    ny = core.ny()
    week = f"{ny.isocalendar()[0]}-W{ny.isocalendar()[1]:02d}"
    done = []
    for knob, new in (changes.get("scales") or {}).items():
        if knob not in dict((k, 1) for k, _, _ in KNOBS):
            continue
        new = max(0, min(10, round(float(new), 1)))
        old = cur["scales"][knob]
        if new != old:
            mine.setdefault("scales", {})[knob] = new
            hist.append({"ts": core.utcnow().isoformat() + "Z", "week": week, "trader": tid, "knob": knob, "old": old,
                         "new": new, "why": why, "by": by})
            done.append(f"{knob} {old}->{new}")
    for sec, new in (changes.get("sectors") or {}).items():
        if sec not in core.CFG["sector_list"]:
            continue
        new = max(0, min(10, round(float(new), 1)))
        old = cur["sectors"].get(sec, 5)
        if new != old:
            mine.setdefault("sectors", {})[sec] = new
            hist.append({"ts": core.utcnow().isoformat() + "Z", "week": week, "trader": tid, "knob": "sector:" + sec,
                         "old": old, "new": new, "why": why, "by": by})
            done.append(f"{sec} {old}->{new}")
    _save("settings.json", allset)
    _save("settings_history.json", hist)
    if done:
        core.log("scales", trader=tid, changes=done, why=why, by=by)
    return done


def apply_doc(doc, by):
    """{"swing": {"scales": {...}, "sectors": {...}, "why": "..."}, ...} or one trader {"trader": "swing", ...}."""
    if "trader" in doc:
        doc = {doc["trader"]: doc}
    out = {}
    for tid, ch in doc.items():
        if tid in core.CFG["traders"] and isinstance(ch, dict):
            out[tid] = apply(tid, ch, ch.get("why", ""), by)
    return out


def history():
    return _load("settings_history.json", [])


def export():
    """For the dashboard: knobs, current values, defaults, what each knob drives (so sliders can preview rules)."""
    out = {"knobs": [{"id": k, "label": l, "help": h} for k, l, h in KNOBS], "sector_list": core.CFG["sector_list"],
           "caps": CAPS, "ints": sorted(INTS), "inverse": [list(x) for x in INVERSE], "traders": {}}
    for tid, t in core.CFG["traders"].items():
        out["traders"][tid] = {"defaults": t["scales"], "base_rules": {k: v for k, v in t.get("rules", {}).items() if isinstance(v, (int, float))},
                               "map": t.get("scale_map", {}), **current(tid), "effective": rules(tid),
                               "uses_sectors": tid in ("day", "swing", "long", "options_aggr", "options_safe")}
    return out
