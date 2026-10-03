"""Backtest harness tests (no network): no lookahead, gap-aware stop fills, report builds."""
import os, sys, tempfile, shutil
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as b


def ok(m): print(f"  [OK] {m}")


tmp = tempfile.mkdtemp()
b.CACHE = os.path.join(tmp, "cache"); os.makedirs(b.CACHE)
b.OUT = os.path.join(tmp, "out"); os.makedirs(b.OUT)
rng = np.random.default_rng(7)
idx = pd.bdate_range("2015-01-02", "2024-12-31")


def frame(seed, drift):
    r = np.random.default_rng(seed)
    c = 50 * np.exp(np.cumsum(r.normal(drift, 0.018, len(idx))))
    v = r.uniform(0.8, 1.2, len(idx)) * 1e6
    v[r.random(len(idx)) < 0.02] *= 4
    return pd.DataFrame({"Open": c * (1 + r.normal(0, .003, len(idx))), "High": c * 1.01,
                         "Low": c * 0.99, "Close": c, "Volume": v}, index=idx)


mk = frame(1, 0.0004)
for name in ["^GSPC", "^DJI"]:
    mk.to_pickle(b._path(name))
for e in b.infra.SECTOR_ETFS.values():
    frame(abs(hash(e)) % 1000, 0.0003).to_pickle(b._path(e))
names = [f"S{i}" for i in range(25)]
for i, t in enumerate(names):
    frame(100 + i, 0.0006).to_pickle(b._path(t))

print("1. Replay is causal: cutting the data later does not change earlier signals")
spw, gates = b.build_gates("2017-01-01", 1)
b._init(spw, gates, {}, "2017-01-01", 1)
cut = pd.Timestamp("2021-12-31")
total = 0
for t in names:
    full = b.process(t)[1]
    d = b.load(t)
    d[d.index <= cut + pd.Timedelta(days=3)].to_pickle(b._path(t))
    short = b.process(t)[1]
    d.to_pickle(b._path(t))
    f = {r["signal"] for r in full if r["signal"] <= "2021-09-30"}
    s = {r["signal"] for r in short if r["signal"] <= "2021-09-30"}
    assert f == s, (t, sorted(f ^ s))
    total += len(f)
ok(f"{total} early signals identical with and without the future data")

print("2. A stop fills at the stop, or at the open when the stock gaps below it")
wk_idx = pd.date_range("2020-01-03", periods=60, freq="W-FRI")
c = np.linspace(50, 80, 60)
wk = pd.DataFrame({"Open": c, "High": c * 1.02, "Low": c * 0.98, "Close": c, "Volume": 1e6}, index=wk_idx)
e = 40
wk.iloc[e, wk.columns.get_loc("Low")] = 40.0
wk.iloc[e, wk.columns.get_loc("Open")] = 44.0     # gaps below a 45 stop
ex = b._exit_sim("T", wk, e, 45.0, float(wk["Open"].iloc[e]))
assert ex["investor"][1] == 44.0 and "entry week" in ex["investor"][2], ex
wk.iloc[e, wk.columns.get_loc("Open")] = 60.0     # opens above, trades down through
ex = b._exit_sim("T", wk, e, 45.0, float(wk["Open"].iloc[e]))
assert ex["investor"][1] == 45.0, ex
ok("gap fills at the open, intraday hit fills at the stop")

print("3. Report builds from trades")
rows = [{"ticker": "A", "signal": "2020-01-03", "entry": "2020-01-10", "verdict": "BREAKOUT - BUY",
         "sector": "Tech", "sector_stage": 2, "signal_close": 10, "entry_px": 10, "gap_pct": 0,
         "stop_pct": -8, "stage_weeks": 3, "ret13": 5.0, "ret26": -2.0, "ret52": 9.0, "lowvol_flag": False,
         "investor_exit": "2020-06-05", "investor_reason": "STOP HIT", "investor_ret": -7.0, "investor_weeks": 20,
         "trader_exit": "2020-06-05", "trader_reason": "STOP HIT", "trader_ret": -7.0, "trader_weeks": 20},
        {"ticker": "B", "signal": "2021-01-08", "entry": "2021-01-15", "verdict": "PULLBACK - BUY",
         "sector": "Tech", "sector_stage": 2, "signal_close": 10, "entry_px": 10, "gap_pct": 0,
         "stop_pct": -8, "stage_weeks": 3, "ret13": 5.0, "ret26": 12.0, "ret52": 30.0, "lowvol_flag": False,
         "investor_exit": "2021-09-03", "investor_reason": "SELL - STAGE 4", "investor_ret": 20.0, "investor_weeks": 30,
         "trader_exit": "2021-09-03", "trader_reason": "SELL - STAGE 4", "trader_ret": 20.0, "trader_weeks": 30}]
pd.DataFrame(rows).to_csv(os.path.join(b.OUT, "trades.csv"), index=False)
open(os.path.join(b.OUT, "done.txt"), "w").write("A\nB\n")
import json
json.dump({"2020|26": [10, 6], "2021|26": [10, 5], "2020|13": [10, 6], "2021|13": [10, 5],
           "2020|52": [10, 6], "2021|52": [10, 5]}, open(os.path.join(b.OUT, "baseline.json"), "w"))
b.cmd_report(None)
S = json.load(open(os.path.join(b.OUT, "summary.json")))
assert S["book_exit_investor"]["win"] == 50.0 and S["hold_26w"]["win"] == 50.0
assert S["hold_26w"]["baseline_win"] == 55.0
assert "Survivorship" in open(os.path.join(b.OUT, "backtest.md")).read()
ok("win rate, baseline and caveats written")
shutil.rmtree(tmp)
print("\nBACKTEST TESTS PASSED")
