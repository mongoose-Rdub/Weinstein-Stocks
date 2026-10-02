"""Tests for universe building and throttle-proof fetching (no network)."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date
import universe_fetch as uf

def ok(m): print(f"  [OK] {m}")

TODAY = date(2026, 10, 2)            # a Friday
def frame(end, n=400, base=100.0, scale=1.0):
    idx = pd.bdate_range(end=end, periods=n)
    c = base + 0.1 * (idx.map(pd.Timestamp.toordinal).to_numpy() - 739000) * 0.7
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c * scale,
                         "Volume": 1000.0}, index=idx)

print("1. Incremental merge")
old = frame("2026-09-18")
new = frame("2026-10-02", n=60)
new.loc[:, ["Open", "High", "Low", "Close"]] = new[["Open", "High", "Low", "Close"]]
m = uf.merge_incremental(old, new)
assert m is not None and m.index[-1] == pd.Timestamp("2026-10-02") and m.index.is_unique
div = new.copy(); div[["Open", "High", "Low", "Close"]] *= 0.99          # a 1% dividend adjustment
md = uf.merge_incremental(old, div)
assert md is not None and abs(md.loc[old.index[-1], "Close"] / div.loc[old.index[-1], "Close"] - 1) < 1e-9
split = new.copy(); split[["Open", "High", "Low", "Close"]] *= 0.5        # a 2:1 split
assert uf.merge_incremental(old, split) is None
ok("appends, rescales for a dividend, and refuses a split (full refetch)")

print("2. Throttle controller")
t = uf.Throttle(chunk=60)
w1 = t.hit(); c1 = t.chunk; w2 = t.hit()
assert c1 == 30 and t.chunk == 15 and w2 > w1 * 1.4 and t.backoff <= 900
for _ in range(3): t.ok()
assert t.chunk > 15 and t.backoff == 0
ok("batch halves and the wait doubles when throttled; both recover")

print("3. Price refresh under throttling")
cache = {}
tickers = [f"T{i:03d}" for i in range(120)]
for i, tk in enumerate(tickers):
    if i < 40: cache[tk] = frame("2026-10-02")                # already current
    elif i < 80: cache[tk] = frame("2026-09-18", base=50)     # needs a short refresh
# 80..119 have no cache: full download. T118, T119 are "delisted" (Yahoo never has them)
calls = {"y": 0}
sleeps = []
def dl_yahoo(batch, period):
    calls["y"] += 1
    if calls["y"] in (2, 3, 4):                                # Yahoo throttles three batches
        return {}
    out = {}
    for tk in batch:
        if tk in ("T118", "T119"): continue
        base = 50 if (tk in cache) else 100
        out[tk] = (None, frame("2026-10-02", n=60 if period == "3mo" else 1000, base=base))
    return out
def dl_stooq(batch, period):
    return {tk: (None, frame("2026-10-02")) for tk in batch if tk == "T118"}
store = dict(cache)
def load(tk): return store.get(tk)
def save(tk, df): store[tk] = df
st = uf.ensure_prices(tickers, today=TODAY, dl_yahoo=dl_yahoo, dl_stooq=dl_stooq,
                      load=load, save=save, sleep=lambda s: sleeps.append(s))
assert st["current"] == 40 and st["incremental"] == 40 and st["full"] == 40, st
assert st["throttle_hits"] >= 1 and max(sleeps) >= 50, (st, max(sleeps))      # it backed off
current = [tk for tk in tickers if store.get(tk) is not None and store[tk].index[-1].date() >= TODAY]
assert len(current) == 119, len(current)                                        # T119 never exists
assert st["stooq"] == 1 and st["failed"] == 1, st                               # T118 rescued by Stooq
ok(f"recovered from throttling, refreshed incrementally, rescued a straggler via Stooq ({st['usable']}/120 usable)")

print("4. Time budget ends fetching cleanly")
store2 = {}
clock = [0.0]
def now(): return clock[0]
def slow(batch, period):
    clock[0] += 400
    return {tk: (None, frame("2026-10-02")) for tk in batch}
st2 = uf.ensure_prices([f"Z{i:03d}" for i in range(500)], today=TODAY, time_budget_min=10,
                       dl_yahoo=slow, dl_stooq=lambda b, p: {}, load=lambda t: store2.get(t),
                       save=lambda t, d: store2.__setitem__(t, d), sleep=lambda s: None, now=now)
assert st2["ended_early"] and 0 < st2["fetched"] < 500, st2
ok("stops at the budget and reports partial coverage")

print("5. Persistent throttling hands over to Stooq")
store3 = {}
st3 = uf.ensure_prices([f"Y{i:03d}" for i in range(300)], today=TODAY, time_budget_min=999,
                       dl_yahoo=lambda b, p: {}, dl_stooq=lambda b, p: {tk: (None, frame("2026-10-02")) for tk in b},
                       load=lambda t: store3.get(t), save=lambda t, d: store3.__setitem__(t, d),
                       sleep=lambda s: None)
assert st3["stooq"] == 300 and st3["usable"] == 300, st3
ok("a Yahoo outage still yields a complete screen through Stooq")

print("6. Universe builder")
rows = pd.DataFrame({"price": [50, 3, 20, 100, 12, 10.05],
                     "vol": [1e6, 1e6, 10, 2e6, 5e5, 40000],
                     "mcap": [5e9, 1e8, 1e7, 9e9, 2e9, 2e8],
                     "sector": ["Technology", "Technology", "Energy", "Health Care", "Finance", "Finance"],
                     "industry": ["", "", "", "", "", "Blank Checks"],
                     "name": ["A Inc", "B Corp", "C Co", "D Inc", "E Inc", "Zeta Acquisition Corp III"]},
                    index=["AAA", "BBB", "CCC", "DDD", "EEE", "SPAC"])
listed = ["AAA", "BBB", "CCC", "DDD", "EEE", "SPAC", "ZZZ"]
tk, sm, meta = uf.build_universe(rows=rows, listed=listed, sp_map={"DDD": "Health"},
                                 sector_path="/tmp/_sec.json", sector_budget=5,
                                 lookup=lambda t: {"AAA": "Technology"}.get(t), sleep=lambda s: None)
# the book sets no price or volume floor: cheap and thin stocks stay; shells and unknown symbols go
assert set(tk) == {"AAA", "BBB", "CCC", "DDD", "EEE"}, tk
assert sm["DDD"] == "Health" and sm["AAA"] == "Tech" and sm["EEE"] == "Finance" and sm["CCC"] == "Energy", sm
assert meta["sector_sources"]["sp"] == 1 and meta["sector_sources"]["yahoo"] == 1, meta
tk2, _, _ = uf.build_universe(rows=rows, listed=listed, sp_map={"DDD": "Health"}, min_price=5,
                              prefilter_dollar_vol=1e6, sector_path="/tmp/_sec2.json", sector_budget=0,
                              sleep=lambda s: None)
assert "BBB" not in tk2 and "CCC" not in tk2          # filters still work when asked for
for p in ("/tmp/_sec.json", "/tmp/_sec2.json"):
    if os.path.exists(p): os.remove(p)
ok("no price/volume floor by default; blank-check shells excluded; sectors from three sources in order")
print("\nUNIVERSE TESTS PASSED")

print("\nSecurity-name filter keeps ordinary shares and ADRs")
import re as _re, stage_vcp_screener as _S
_rx=_re.compile("|".join(_S.EXCLUDE_NAME_REGEX))
for _n in ("Nokia Corporation Sponsored American Depositary Shares","Wright Medical Group N.V. Ordinary Shares",
           "Bright Horizons Family Solutions Inc. - Common Stock","Copyright Clearance Center Common Stock",
           "Toyota Motor Corporation American Depositary Shares","ASML Holding N.V. - New York Registry Shares"):
    assert not _rx.search(_n.lower()), _n
for _n in ("Cantor Equity Partners V, Inc. Class A Ordinary Shares","Foo Acquisition Corp. Class A Common Stock",
           "Acme Corp Warrants","Acme Corp Rights","Acme Acquisition Corp Units",
           "Bank of X Depositary Shares, each representing a 1/40th interest in a share of 5.5% Series B Preferred Stock",
           "Acme Corp 6.5% Notes due 2030","XYZ 7.25% Fixed Rate Cumulative Preferred"):
    assert _rx.search(_n.lower()), _n
print("  [OK] ADRs and names containing 'right'/'unit' are kept; warrants, rights, units, preferreds, notes dropped")

print("\nA Friday bar cached before the close is refreshed, not trusted")
_store = {"AAA": frame("2026-10-02"), "BBB": frame("2026-10-02")}
_fetched = []
def _dl(batch, period):
    _fetched.extend(batch)
    return {t: (None, frame("2026-10-02", n=60)) for t in batch}
_close = uf.market_close_epoch(TODAY)
_written = {"AAA": _close - 6 * 3600, "BBB": _close + 600}      # morning vs just after the close
_st = uf.ensure_prices(["AAA", "BBB"], today=TODAY, dl_yahoo=_dl, dl_stooq=lambda b, p: {},
                       load=lambda t: _store.get(t), save=lambda t, d: _store.__setitem__(t, d),
                       sleep=lambda s: None, written_at=lambda t: _written.get(t))
assert _fetched == ["AAA"], _fetched
assert _st["current"] == 1 and _st["incremental"] == 1
print("  [OK] morning-cached Friday bar is refetched; one written after the close is kept")
