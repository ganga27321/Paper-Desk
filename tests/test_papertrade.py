import datetime as dt

import pandas as pd
import pytest

from papertrade import core, engine


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "STATE", tmp_path)
    yield


def bars(rows, start="2026-11-09 15:00"):  # 15:00 UTC = 10:00 New York (in session)
    t0 = pd.Timestamp(start)
    idx = [t0 + pd.Timedelta(minutes=5 * i) for i in range(len(rows))]
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def order(**kw):
    o = {"id": "o1", "kind": "stock", "symbol": "XYZ", "qty": 100, "side": "long", "entry_type": "stop", "price": 10.0,
         "stop": 9.5, "target": 11.0, "why": "test", "source": "autopilot", "meta": {},
         "created": "2026-11-09T14:59:00", "valid_until": "2026-11-09T21:00:00"}
    o.update(kw)
    return o


def test_breakout_fill_then_target():
    a = core.load_accounts()["day"]
    a["pending"] = [order()]
    b = bars([[9.8, 9.9, 9.7, 9.85, 1], [9.9, 10.2, 9.9, 10.1, 1], [10.1, 11.2, 10.0, 11.1, 1]])
    engine.replay("day", a, {"XYZ": b}, 3000, dt.datetime(2026, 11, 9, 16, 0))
    assert not a["pending"] and not a["positions"]
    t = core.read_trades()
    assert [x["action"] for x in t] == ["BUY", "SELL"]
    assert float(t[0]["price"]) == 10.0 and float(t[1]["price"]) == 11.0
    assert a["cash"] > 3000 + 90  # +$100 less slippage


def test_stop_beats_target_in_same_bar():
    a = core.load_accounts()["swing"]
    a["pending"] = [order(entry_type="market")]
    b = bars([[10, 10.1, 9.9, 10, 1], [10, 11.5, 9.0, 10, 1]])
    engine.replay("swing", a, {"XYZ": b}, 3000, dt.datetime(2026, 11, 9, 16, 0))
    t = core.read_trades()
    assert t[-1]["why"] == "stop hit" and float(t[-1]["price"]) == 9.5


def test_orders_never_fill_on_bars_before_they_were_placed():
    a = core.load_accounts()["day"]
    a["pending"] = [order(created="2026-11-09T15:07:00")]
    b = bars([[9.9, 10.5, 9.9, 10.4, 1], [10.4, 10.45, 10.3, 10.35, 1]])  # only bar 1 spikes, bar 2 starts 15:05
    engine.replay("day", a, {"XYZ": b}, 3000, dt.datetime(2026, 11, 9, 16, 0))
    assert a["pending"] and not a["positions"]


def test_tfsa_trader_ignores_after_hours_bars():
    a = core.load_accounts()["swing"]
    a["pending"] = [order(entry_type="market")]
    b = bars([[10, 10, 10, 10, 1]], start="2026-11-09 22:00")  # 17:00 New York
    engine.replay("swing", a, {"XYZ": b}, 3000, dt.datetime(2026, 11, 9, 23, 0))
    assert a["pending"]


def test_future_short_pnl_and_margin():
    a = core.load_accounts()["futures"]
    o = order(kind="future", symbol="MGC", qty=1, side="short", entry_type="market", stop=4100, target=3900,
              meta={"multiplier": 10, "yahoo": "GC=F"})
    a["pending"] = [o]
    b = bars([[4000, 4001, 3999, 4000, 1], [4000, 4005, 3990, 3995, 1], [3995, 3996, 3890, 3900, 1]])
    engine.replay("futures", a, {"GC=F": b}, 3000, dt.datetime(2026, 11, 9, 16, 0))
    t = core.read_trades()
    assert t[0]["action"] == "SHORT" and t[-1]["action"] == "COVER"
    assert float(t[-1]["pnl"]) == pytest.approx(10 * 100 - 2.5, abs=0.01)


def test_tfsa_rules():
    assert core.tfsa_check("swing", {"kind": "stock", "side": "short", "qty": 1}) == "no short selling in a TFSA"
    assert core.tfsa_check("swing", {"kind": "stock", "qty": 1.5}) == "whole shares only"
    assert core.tfsa_check("options_safe", {"kind": "option", "action": "SELL_TO_OPEN", "right": "P"}).startswith("naked puts")
    assert core.tfsa_check("options_safe", {"kind": "option", "action": "SELL_TO_OPEN", "right": "P", "cash_secured": True}) is None
    assert core.tfsa_check("long", {"kind": "crypto", "qty": 1}).startswith("crypto")
    assert core.tfsa_check("crypto", {"kind": "crypto", "qty": 0.5}) is None  # sandbox


def test_cash_secured_put_reserves_cash():
    a = core.load_accounts()["options_safe"]
    o = order(kind="option", symbol="PFE P28", qty=1, action="SELL_TO_OPEN", entry_type="market",
              meta={"underlying": "PFE", "expiry": "2026-12-18", "right": "P", "strike": 28.0, "contract": "X"})
    engine.open_position("options_safe", a, o, 0.60, dt.datetime(2026, 11, 9, 15), 3000)
    assert a["cash"] == pytest.approx(3000 + 60 - 0.99)
    assert engine.buying_power(a) == pytest.approx(a["cash"] - 2800)


def test_weekly_bonus_goes_to_biggest_earner_once():
    import datetime as dt
    from papertrade import weekly
    core.append_equity("2026-11-02T15:00:00Z", [("day", 3000, 0, 3000), ("swing", 3000, 0, 3000)])
    core.append_equity("2026-11-06T21:00:00Z", [("day", 3000, 0, 3150), ("swing", 3000, 0, 3400)])
    core.save_accounts(core.load_accounts())
    w = weekly.award(dt.date(2026, 11, 6))
    assert w["winner"] == "swing" and w["bonus"] == 200
    a = core.load_accounts()["swing"]
    assert a["cash"] == 3200 and a["deposits"][0]["amount"] == 200
    assert weekly.award(dt.date(2026, 11, 6))["winner"] == "swing"   # not paid twice
    assert core.load_accounts()["swing"]["cash"] == 3200


def test_scales_change_rules_and_history():
    from papertrade import settings
    base = settings.rules("swing")["max_positions"]
    settings.apply("swing", {"scales": {"frequency": 10.5}}, "test", "you")   # clamped to 10
    assert settings.current("swing")["scales"]["frequency"] == 10
    assert settings.rules("swing")["max_positions"] > base
    assert settings.history()[-1]["by"] == "you"
    assert settings.sector_mult("swing", "Energy") == 1.0
    settings.apply("swing", {"sectors": {"Energy": 0}}, "test", "you")
    assert settings.sector_mult("swing", "Energy") == 0.0


def test_guards_halt_trader_and_council_levels():
    from papertrade import run
    plan = {"levels": {"XYZ": {"stop": 9.0, "no_buy_above": 12}, "ABC.TO": {"stop": 20}},
            "limits": {"swing": {"max_day_loss_pct": 2}}}
    lv = run._levels(plan, 1.4)
    assert lv["XYZ"]["stop"] == pytest.approx(12.6) and lv["ABC.TO"]["stop"] == 20   # US levels converted to C$
    assert run._limits("swing", plan)["max_day_loss_pct"] == 2
    assert run._limits("options_safe", None)["max_day_loss_pct"] == 3


def test_plan_without_stop_is_rejected(tmp_path):
    import json
    from papertrade import meeting
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"date": "2026-11-09", "market_view": "x", "tabled": [], "levels": {"NVDA": {"target": 1}},
                             "traders": {"swing": {"orders": [{"symbol": "AMD", "why": "x"}]}}}))
    assert not meeting.validate(p)
