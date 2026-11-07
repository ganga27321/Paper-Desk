import json
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


def test_weekly_bonus_goes_to_biggest_earner_once(monkeypatch):
    import datetime as dt
    from papertrade import weekly
    monkeypatch.setitem(core.CFG, "bonus_hold", [])
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


def test_one_minute_bars_fill_without_waiting_five_minutes():
    a = core.load_accounts()["day"]
    a["pending"] = [order(created="2026-11-09T14:59:00")]
    t0 = pd.Timestamp("2026-11-09 15:00")
    b = pd.DataFrame([[9.9, 10.05, 9.9, 10.02, 1], [10.02, 10.1, 10.0, 10.08, 1]], columns=["open", "high", "low", "close", "volume"],
                     index=[t0, t0 + pd.Timedelta(minutes=1)])
    engine.replay("day", a, {"XYZ": b}, 3000, dt.datetime(2026, 11, 9, 15, 2))   # 2 minutes in: both 1-min bars are complete
    assert a["positions"] and a["positions"][0]["entry"] == 10.0


def test_dax_every_run_others_every_15_minutes():
    from papertrade import run
    assert run.due() is None            # first run of the day: everyone
    assert run.due() == ["day"]         # 5 minutes later: Dax only


def test_scale_out_half_at_1r_then_breakeven():
    a = core.load_accounts()["swing"]
    a["pending"] = [order(entry_type="market", qty=10, stop=9.0, target=13.0, scale_out_r=1)]
    b = bars([[10, 10.1, 9.95, 10, 1], [10, 11.2, 10.0, 11.1, 1], [11.1, 11.2, 9.9, 10.0, 1]])
    engine.replay("swing", a, {"XYZ": b}, 3000, dt.datetime(2026, 11, 9, 16, 0))
    t = core.read_trades()
    assert [x["action"] for x in t] == ["BUY", "SELL", "SELL"]
    assert float(t[1]["qty"]) == 5 and float(t[1]["price"]) == 11.0          # half at 1R
    assert float(t[2]["price"]) == 10.0 and "stop" in t[2]["why"]           # rest stopped at breakeven, not at 9


def test_percent_trailing_stop():
    from papertrade.engine import _trail
    p = {"entry": 100.0, "stop": 80.0, "side": "long", "trail_pct": 10}
    _trail(p, {"high": 130.0})
    assert p["stop"] == 117.0


def test_cooldown_after_stop_and_regime_sizing(monkeypatch):
    from papertrade import edge
    core.append_trade({"ts": "2026-11-09T15:00:00Z", "trader": "swing", "action": "SELL", "kind": "stock", "symbol": "XYZ",
                       "qty": 1, "price": 9, "fee": 0, "value": 9, "pnl": -1, "pct_of_equity": 0, "why": "stop hit",
                       "source": "autopilot", "tfsa_legal": "yes"})
    assert edge.cooling("swing", "XYZ", dt.date(2026, 11, 11))
    assert not edge.cooling("swing", "XYZ", dt.date(2026, 11, 20))
    assert edge.size_mult("swing", {"mode": "risk_off"}) == 0.6
    assert edge.size_mult("day", {"mode": "neutral"}) == 1.0


def test_clock_and_released_events_must_have_results(tmp_path):
    import json
    from papertrade import meeting
    c = meeting.clock(dt.datetime(2026, 11, 9, 13, 40))   # 8:40 ET on a Monday
    assert c["status"] == "premarket" and "50 min" in c["note"]
    today = core.ny().date().isoformat()
    p = tmp_path / "plan.json"
    base = {"date": today, "market_view": "x", "tabled": [], "traders": {},
            "scenarios": [{"if": "yields jump", "then": "tighten stops"}] * 3}   # plans from 2026-11-09 need scenarios
    p.write_text(json.dumps({**base, "events": [{"time_et": "00:00", "name": "jobs", "status": "upcoming"}]}))
    assert not meeting.validate(p)            # already out, still called upcoming
    p.write_text(json.dumps({**base, "events": [{"time_et": "00:00", "name": "jobs", "status": "released", "actual": "+142k"}]}))
    assert meeting.validate(p)


def test_long_trader_stop_is_always_a_positive_percent_below_entry():
    """Regression: Lena's stop width once produced a negative stop_pct, putting her stop above the buy price."""
    from papertrade import settings
    for scale in range(0, 11):
        settings.apply("long", {"scales": {"stop_width": scale}}, "test", "you")
        r = settings.rules("long")
        assert "stop_pct" not in r
        assert 0 < r["long_stop_pct"] <= 40
        assert round(100 * (1 - r["long_stop_pct"] / 100), 2) < 100  # stop below entry for every scale


def test_forfeited_week_pays_nothing_and_does_not_carry_over(monkeypatch):
    import datetime as dt
    from papertrade import weekly
    monkeypatch.setitem(core.CFG, "bonus_hold", ["2026-W45"])
    core.append_equity("2026-11-02T15:00:00Z", [("day", 3000, 0, 3000), ("swing", 3000, 0, 3000)])
    core.append_equity("2026-11-06T21:00:00Z", [("day", 3000, 0, 3150), ("swing", 3000, 0, 3400)])
    core.save_accounts(core.load_accounts())
    assert weekly.award(dt.date(2026, 11, 6)) is None
    assert core.load_accounts()["swing"]["cash"] == 3000            # nothing paid for the held week
    core.append_equity("2026-11-13T21:00:00Z", [("day", 3000, 0, 3100), ("swing", 3000, 0, 3500)])
    w = weekly.award(dt.date(2026, 11, 13))                         # next week pays only its own C$200
    assert w["bonus"] == 200


def _detailed_plan(**over):
    blk = {"when": {"type": "time_after", "time_et": "11:00"}, "note": "if plan A has not filled", "orders": []}
    tr = {"view": "v", "failsafe": "stand aside if the day-loss limit hits", "orders": [],
          "playbook": [{"if": "CPI hot", "then": "no new longs"}] * 3, "plan_b": [blk], "plan_c": [blk]}
    p = {"date": "2026-11-09", "market_view": "x", "tabled": [], "events": [],
         "scenarios": [{"if": "yields jump", "then": "tighten stops"}] * 3, "traders": {"swing": tr}}
    p.update(over)
    return p


def test_detailed_plan_needs_scenarios_playbook_plan_b_c_and_failsafe(tmp_path, capsys):
    from papertrade import meeting
    f = tmp_path / "p.json"
    f.write_text(json.dumps(_detailed_plan()))
    assert meeting.validate(str(f))
    bad = _detailed_plan()
    del bad["traders"]["swing"]["plan_c"], bad["traders"]["swing"]["failsafe"]
    bad["scenarios"] = []
    f.write_text(json.dumps(bad))
    assert not meeting.validate(str(f))
    out = capsys.readouterr().out
    assert "plan_c is required" in out and "missing failsafe" in out and "scenarios" in out
    bad = _detailed_plan()
    bad["traders"]["swing"]["plan_b"][0]["when"] = {"type": "gut_feeling"}
    f.write_text(json.dumps(bad))
    assert not meeting.validate(str(f))


def test_when_met_triggers(monkeypatch):
    from papertrade import strategies, data
    ny = core.ny(dt.datetime(2026, 11, 9, 16, 0))   # 11:00 ET
    ctx = {"now_ny": ny, "date": "2026-11-09"}
    a = {"positions": [], "pending": [], "day": {"start_eq": 3000}, "equity_last": 2700}
    monkeypatch.setattr(data, "last_price", lambda s: {"^VIX": 31.0, "SPY": 700.0}.get(s))
    wm = strategies.when_met
    assert wm({"type": "time_after", "time_et": "10:30"}, "swing", a, ctx)
    assert not wm({"type": "time_after", "time_et": "12:00"}, "swing", a, ctx)
    assert wm({"type": "vix_above", "level": 30}, "swing", a, ctx)
    assert wm({"type": "price_below", "symbol": "SPY", "level": 710}, "swing", a, ctx)
    assert wm({"type": "day_loss_over", "pct": 5}, "swing", a, ctx)
    assert not wm({"type": "nonsense"}, "swing", a, ctx)


def test_plan_b_orders_go_live_only_when_their_condition_is_met():
    from papertrade import strategies
    blk = {"when": {"type": "time_after", "time_et": "11:00"}, "note": "plan A did not fill",
           "orders": [{"symbol": "XYZ", "kind": "stock", "entry_type": "limit", "price": 10.0, "stop": 9.5, "qty": 5, "why": "fallback"}]}
    plan = {"traders": {"swing": {"orders": [], "plan_b": [blk]}}}
    a = core.load_accounts()["swing"]
    base = {"plan": plan, "date": "2026-11-09", "fx": 1.0, "prices": {"XYZ": 10.0}, "equity": 3000}
    early = strategies.plan_orders("swing", a, {**base, "now_ny": core.ny(dt.datetime(2026, 11, 9, 14, 0))})   # 09:00 ET
    assert early == []
    late = strategies.plan_orders("swing", a, {**base, "now_ny": core.ny(dt.datetime(2026, 11, 9, 16, 30))})   # 11:30 ET
    assert len(late) == 1 and "Plan B" in late[0]["why"]
    again = strategies.plan_orders("swing", a, {**base, "now_ny": core.ny(dt.datetime(2026, 11, 9, 17, 0))})
    assert again == []   # each block fires once


def test_lessons_are_saved_deduplicated_capped_and_fed_to_the_meeting(tmp_path):
    from papertrade import weekly, meeting
    weekly.add_lessons({"week": "2026-W45", "lessons": {"day": ["ORB fails after a rout", "ORB fails after a rout", " "]}}, "2026-W45")
    assert [m["lesson"] for m in weekly.lessons()["day"]] == ["ORB fails after a rout"]
    weekly.add_lessons({"week": "2026-W46", "lessons": {"day": [f"l{i}" for i in range(20)]},
                        "retire_lessons": {"day": ["l3"]}}, "2026-W46")
    got = [m["lesson"] for m in weekly.lessons()["day"]]
    assert len(got) == weekly.LESSONS_KEEP and "l3" not in got and got[-1] == "l19"
    assert meeting._lessons()["day"] == got
    p = _detailed_plan(); p["traders"]["day"] = dict(p["traders"]["swing"])
    f = tmp_path / "p.json"
    f.write_text(json.dumps(p))
    assert not meeting.validate(str(f))          # lessons exist for Dax but the plan does not say which it used
    p["traders"]["day"]["lessons_applied"] = ["l19: stood aside after the rout"]
    f.write_text(json.dumps(p))
    assert meeting.validate(str(f))


def test_event_times_compare_as_times_not_text(tmp_path):
    """'8:30' sorts after '10:05' as text; the clock check must still see that 8:30 has passed."""
    from papertrade import meeting
    assert meeting._hm("8:30") == dt.time(8, 30) and meeting._hm("08:30") == dt.time(8, 30)
    assert meeting._hm("2:00pm") == dt.time(14, 0) and meeting._hm("soon") is None
    today = core.ny().date().isoformat()
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"date": today, "market_view": "x", "tabled": [], "traders": {},
                             "scenarios": [{"if": "a", "then": "b"}] * 3,
                             "events": [{"time_et": "0:01", "name": "jobs", "status": "upcoming"}]}))
    assert not meeting.validate(p)                       # unpadded time still counts as released
    assert meeting.validate(p, check_clock=False)        # later re-checks (sharing) skip the clock


def test_daily_plan_cannot_change_scales(tmp_path, capsys):
    from papertrade import meeting
    f = tmp_path / "p.json"
    f.write_text(json.dumps(_detailed_plan(adjustments={"swing": {"scales": {"aggression": 9}}})))
    assert not meeting.validate(str(f))
    assert "only in the Saturday review" in capsys.readouterr().out
    review = {"week": "2026-W46", "weekly_review": {}, "lessons": {}, "adjustments": {"options_safe": {"scales": {"aggression": 6}}}}
    f.write_text(json.dumps(review))
    assert not meeting.validate(str(f))                  # Theo stays the safe player


def test_review_is_applied_once_wherever_it_was_applied_from(monkeypatch):
    from papertrade import weekly, settings
    monkeypatch.setattr(weekly, "build", lambda d=None: "pdf")
    ny = core.ny()
    tag = f"{ny.isocalendar()[0]}-W{ny.isocalendar()[1]:02d}"
    (core.STATE / "reviews").mkdir()
    (core.STATE / "reviews" / f"{tag}.json").write_text(json.dumps(
        {"week": tag, "weekly_review": {}, "lessons": {}, "adjustments": {"swing": {"scales": {"aggression": 3}, "why": "t"}}}))
    # an older run recorded the review by a full path from another machine
    (core.STATE / "reviews_applied.json").write_text(json.dumps([f"/home/someone/papertrade/state/reviews/{tag}.json"]))
    weekly.review()
    assert settings.current("swing")["scales"]["aggression"] != 3   # not applied a second time
    (core.STATE / "reviews_applied.json").write_text("[]")
    weekly.review()
    assert settings.current("swing")["scales"]["aggression"] == 3
    assert json.loads((core.STATE / "reviews_applied.json").read_text()) == [f"{tag}.json"]


def test_fx_falls_back_to_the_last_rate_seen(monkeypatch):
    from papertrade import data
    monkeypatch.setattr(data, "last_price", lambda s: 1.3712)
    assert data.usdcad() == 1.3712
    monkeypatch.setattr(data, "last_price", lambda s: None)
    assert data.usdcad() == 1.3712                        # Yahoo down: last rate, not a made-up 1.40


def test_notes_never_fail_a_run_and_the_plan_is_shared_once(monkeypatch, tmp_path):
    import requests
    from papertrade import notify, meeting
    monkeypatch.setenv("NTFY_TOPIC", "test-topic")
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    def down(*a, **k):
        raise requests.ConnectionError("ntfy unreachable")
    monkeypatch.setattr(notify.requests, "post", down)
    monkeypatch.setattr(notify.requests, "get", down)
    assert notify.send("t", "x") is False                 # warns, never raises
    assert notify.sent_today("t", "x") is None            # unknown, not "not sent"
    sent = []
    class Ok:
        def raise_for_status(self):
            pass
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: sent.append(k["json"]["title"]) or Ok())
    f = tmp_path / "plan.json"
    f.write_text(json.dumps({"date": "2026-11-09", "market_view": "x", "tabled": [], "traders": {}}))
    meeting.share(str(f))
    meeting.share(str(f))
    assert sent == ["Paper Desk 7 AM plan 2026-11-09"]
