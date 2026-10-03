"""Backtest of the Weinstein screener: how often did an Active Buy end up positive?

It replays the SAME code the Friday run uses (analyse + verdict + the market and
sector gates + the book's selling rules in position_status) on past Fridays, using
only data that existed on each Friday. Nothing here changes the screener.

    python backtest.py fetch  --universe all   # download full price history (resumable)
    python backtest.py run    --start 2000-01-01
    python backtest.py report

HOW A TRADE IS DEFINED (every choice below is ours, not the book's, and is stated in
the report):
  * Signal: a stock the Friday screen would list as an ACTIVE BUY on that Friday's
    close (BREAKOUT / CONTINUATION / PULLBACK - BUY, stop not wider than 15%, sector
    Stage 1 or 2, S&P and Dow not in Stage 4).
  * Entry: the OPEN of the next week (the first trading day after the signal).
  * One position per stock at a time: a new signal is ignored while the book-exit
    trade is still open.
  * Book exit: position_status(), the same sell rules the positions page uses (stop
    hit p.176/184, Stage 4 p.39, Stage 3 p.36, failed breakout p.116; trader style
    adds the 30-week-MA exit p.196). A stop fills at the stop price, or at the open if
    the stock gaps below it. Any other sell signal is read on Friday's close and
    executed at the next Monday open. "Sell half at Stage 3" is treated as selling
    all, and "take partial" signals are ignored (hold).
  * Fixed horizons: the close 13, 26 and 52 weeks after the entry week, ignoring stops.
  * Baseline: the same Fridays, EVERY stock, held the same horizons. An Active Buy
    that beats this is adding something; one that does not is only riding the market.

KNOWN LIMITS (also printed in the report): the price universe is today's listed stocks, so
companies that were delisted or went bankrupt are missing (survivorship bias: results read
better than reality); sectors use today's classification; sector ETFs stand in for industry
groups and some did not exist early on (then the sector gate is skipped and flagged);
no news, no contrary-opinion gauges, no commissions or slippage.
"""
import argparse
import json
import math
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weinstein_pure as w
import stage_vcp_screener as infra
import universe_fetch as uf

CACHE = ".bt_cache"
OUT = "data/backtest"
HORIZONS = (13, 26, 52)
WINDOW = 260                                  # 5 years of weekly bars, as the live screen sees
BUY = w.BUY_VERDICTS
CFG = dict(w.CFG)
CFG["resistance_mode"] = "strict"             # the live default


# ----------------------------------------------------------------- data ------
def _path(t):
    return os.path.join(CACHE, t.replace("^", "_IDX_").replace("/", "_") + ".pkl")


def load(t):
    try:
        return pd.read_pickle(_path(t))
    except Exception:
        return None


def weekly_of(daily):
    wk = infra._to_weekly(daily)
    if len(wk) and wk.index[-1] > daily.index[-1].normalize() + pd.Timedelta(days=1) \
            and daily.index[-1].weekday() < 3:
        wk = wk.iloc[:-1]                     # a week still in progress
    return wk


def cmd_fetch(a):
    os.makedirs(CACHE, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    deadline = time.time() + a.time_budget * 60
    upath = os.path.join(OUT, "universe.json")
    if a.universe == "cache":
        import glob
        tickers = [os.path.basename(p)[:-4] for p in glob.glob(os.path.join(CACHE, "*.pkl"))
                   if not os.path.basename(p).startswith("_IDX_")]
        smap = {}
    elif os.path.exists(upath) and not a.rebuild_universe:
        j = json.load(open(upath))
        tickers, smap = j["tickers"], j["smap"]
    else:
        tickers, smap, meta = uf.build_universe(min_price=0.0, prefilter_dollar_vol=0,
                                                sector_path="data/sectors.json", sector_budget=0,
                                                deadline=time.time() + 600)
        json.dump({"tickers": tickers, "smap": smap}, open(upath, "w"))
        print("universe", meta)
    need = ["^GSPC", "^DJI", "SPY"] + list(infra.SECTOR_ETFS.values())
    queue = [t for t in need + list(tickers) if not os.path.exists(_path(t))]
    print(f"{len(tickers)} tickers; {len(queue)} still to download")
    th = uf.Throttle(chunk=40, hi=60)
    tries = {}
    done = 0
    while queue and time.time() < deadline:
        batch, queue = queue[:th.chunk], queue[th.chunk:]
        got = infra._download_batch_yahoo(batch, "max", True)
        if not got:
            th.hit()
            wait = th.backoff * 0.5
            for t in batch:
                tries[t] = tries.get(t, 0) + 1
            queue += [t for t in batch if tries[t] < 3]
            print(f"  throttled; waiting {wait:.0f}s")
            time.sleep(min(wait, max(0, deadline - time.time())))
            continue
        th.ok()
        for t, (_, d) in got.items():
            d = d[["Open", "High", "Low", "Close", "Volume"]]
            d.to_pickle(_path(t))
        miss = [t for t in batch if t not in got]
        for t in miss:
            tries[t] = tries.get(t, 0) + 1
        queue += [t for t in miss if tries[t] < 2]
        done += len(got)
        if done % 400 < th.chunk:
            print(f"  downloaded {done}")
        time.sleep(th.pause)
    print("fetch finished;", len(queue), "left")


# ---------------------------------------------------------------- gates ------
def stage_at(weekly_full, F, cfg=CFG):
    win = weekly_full.loc[:F].tail(WINDOW)
    if len(win) < cfg["ma_length"] + 12:
        return None
    ma = infra.moving_average(win["Close"], cfg["ma_length"], cfg["ma_type"])
    if np.isnan(ma.iloc[-1]):
        return None
    s = classify_last(win, ma, cfg)
    return s


def classify_last(win, ma, cfg):
    st = w.classify(win, ma, cfg)[0]
    v = st.iloc[-1]
    return None if pd.isna(v) else int(v)


def build_gates(start, step):
    """{Friday: {'mkt':stage,'dow':stage,'sec':{sector:stage}}}, computed causally."""
    sp = load("^GSPC")
    if sp is None:
        raise SystemExit("run fetch first (no ^GSPC history)")
    spw = weekly_of(sp)
    dow = load("^DJI")
    doww = weekly_of(dow) if dow is not None else None
    secw = {}
    for name, etf in infra.SECTOR_ETFS.items():
        d = load(etf)
        if d is not None:
            secw[name] = weekly_of(d)
    gates = {}
    for F in spw.index[spw.index >= pd.Timestamp(start)]:
        if (F.toordinal() // 7) % step:
            continue
        g = {"mkt": stage_at(spw, F), "dow": stage_at(doww, F) if doww is not None else None, "sec": {}}
        for name, f in secw.items():
            g["sec"][name] = stage_at(f, F)
        gates[F] = g
    return spw, gates


# --------------------------------------------------------------- workers -----
G = {}


def _init(spw, gates, smap, start, step):
    G.update(spw=spw, gates=gates, smap=smap, start=pd.Timestamp(start), step=step)


def _fwd(close, e, entry_px):
    out = {}
    n = len(close)
    for h in HORIZONS:
        out[f"ret{h}"] = (close[e + h] / entry_px - 1) * 100 if e + h < n else float("nan")
    return out


def _exit_sim(t, weekly, e, init_stop, entry_px):
    """Walk forward week by week with the book's sell rules for investor and trader."""
    n = len(weekly)
    O, L, C = (weekly[c].to_numpy() for c in ("Open", "Low", "Close"))
    res = {}
    # a stop under the entry week's own low is hit before the first full week passes
    if init_stop and L[e] <= init_stop:
        px = min(init_stop, O[e])
        for s in ("investor", "trader"):
            res[s] = (e, px, "STOP HIT (entry week)")
    pos = {"ticker": t, "buy_date": weekly.index[e], "buy_price": entry_px}
    pending = {}                       # "sell on the first rally" (p.116) seen, rally not yet
    flagged = False
    j = e
    while j < n and len(res) < 2:
        win = weekly.iloc[max(0, j - WINDOW + 1):j + 1]
        ma = infra.moving_average(win["Close"], CFG["ma_length"], CFG["ma_type"])
        stg = w.classify(win, ma, CFG)[0]
        for style in ("investor", "trader"):
            if style in res:
                continue
            ps = w.position_status(dict(pos, style=style), win, ma, stg, CFG)
            st = ps["status"]
            if st == "SELL ON FIRST RALLY":
                # the book says sell on the first RALLY, not at once: our reading is the
                # first weekly close above the purchase price, sold at the next open
                flagged = True
                pending[style] = True
                if C[j] > entry_px:
                    k = j + 1
                    res[style] = (k, O[k] if k < n else C[j], st)
                continue
            if not st.startswith("SELL"):
                if pending.get(style) and C[j] > entry_px:
                    k = j + 1
                    res[style] = (k, O[k] if k < n else C[j], "SELL ON FIRST RALLY")
                continue
            if st == "SELL - STOP HIT" and ps.get("hit"):
                hi = weekly.index.get_indexer([pd.Timestamp(ps["hit"][0])])[0]
                hi = j if hi < 0 else hi
                res[style] = (hi, min(float(ps["hit"][1]), O[hi]), "STOP HIT")
            else:
                k = j + 1
                res[style] = (k, O[k] if k < n else C[j], st)
        j += 1
    for s in ("investor", "trader"):
        if s not in res:
            res[s] = (n - 1, C[-1], "OPEN")
    res["flag"] = flagged
    return res


def process(t):
    """-> (ticker, trades, baseline) for one stock over every eligible Friday."""
    daily = load(t)
    if daily is None or len(daily) < 400:
        return t, [], {}
    daily = daily[~daily.index.duplicated()].sort_index()
    daily = daily[(daily["Close"] > 0) & (daily["Volume"] >= 0)].dropna()
    weekly = weekly_of(daily)
    n = len(weekly)
    need = CFG["ma_length"] + 12
    if n < need + 2:
        return t, [], {}
    close = weekly["Close"].to_numpy()
    opn = weekly["Open"].to_numpy()
    ma = infra.moving_average(weekly["Close"], CFG["ma_length"], CFG["ma_type"]).to_numpy()
    sector = G["smap"].get(t)
    spw = G["spw"]
    trades, base = [], {}
    blocked_until = -1
    didx = daily.index
    for i in range(need, n - 1):
        F = weekly.index[i]
        g = G["gates"].get(F)
        if g is None:
            continue
        # baseline: every stock, every sampled Friday, held the same horizons
        yr = str(F.year)
        for h in HORIZONS:
            if i + 1 + h < n:
                b = base.setdefault(f"{yr}|{h}", [0, 0])
                b[0] += 1
                b[1] += int(close[i + 1 + h] > opn[i + 1])
        if i <= blocked_until:
            continue
        if g["mkt"] == 4 or g["dow"] == 4:
            continue
        gs = g["sec"].get(sector) if sector else None
        if sector and gs is not None and gs not in (1, 2):
            continue
        if not close[i] > ma[i]:
            continue                              # verdict would be AVOID - BELOW MA
        win = weekly.iloc[max(0, i - WINDOW + 1):i + 1]
        dd = daily.iloc[:didx.searchsorted(F, side="right")].tail(1300)
        idx = G["spw"].loc[:F].tail(WINDOW)
        try:
            r = w.analyse(t, win, dd, idx, CFG, group_stage=gs)
        except Exception:
            continue
        if not r or r["verdict"] not in BUY or r.get("wide_stop"):
            continue
        e = i + 1
        entry_px = float(opn[e])
        if not entry_px > 0:
            continue
        ex = _exit_sim(t, weekly, e, r.get("stop"), entry_px)
        row = {"ticker": t, "signal": F.date().isoformat(), "entry": weekly.index[e].date().isoformat(),
               "verdict": r["verdict"], "sector": sector or "", "sector_stage": gs if gs is not None else "",
               "signal_close": round(float(close[i]), 4), "entry_px": round(entry_px, 4),
               "gap_pct": round((entry_px / close[i] - 1) * 100, 2),
               "stop_pct": r.get("stop_pct"), "stage_weeks": r.get("stage_weeks")}
        row.update({k: round(v, 2) if v == v else "" for k, v in _fwd(close, e, entry_px).items()})
        row["lowvol_flag"] = bool(ex["flag"])
        for s in ("investor", "trader"):
            xi, xpx, why = ex[s]
            row[f"{s}_exit"] = weekly.index[min(xi, n - 1)].date().isoformat()
            row[f"{s}_reason"] = why
            row[f"{s}_ret"] = round((xpx / entry_px - 1) * 100, 2)
            row[f"{s}_weeks"] = int(max(xi - e, 0))
        trades.append(row)
        blocked_until = ex["investor"][0] if ex["investor"][2] != "OPEN" else n
    return t, trades, base


def cmd_run(a):
    os.makedirs(OUT, exist_ok=True)
    upath = os.path.join(OUT, "universe.json")
    if os.path.exists(upath):
        j = json.load(open(upath))
        tickers, smap = j["tickers"], j["smap"]
    else:
        import glob
        tickers = [os.path.basename(p)[:-4] for p in glob.glob(os.path.join(CACHE, "*.pkl"))]
        smap = {}
    tickers = [t for t in tickers if not t.startswith("_IDX_") and t not in infra.SECTOR_ETFS.values() and t != "SPY"]
    if a.max_tickers:
        import random
        random.Random(1).shuffle(tickers)
        tickers = tickers[:a.max_tickers]
    donep = os.path.join(OUT, "done.txt")
    tradesp = os.path.join(OUT, "trades.csv")
    basep = os.path.join(OUT, "baseline.json")
    sig = f"{a.start}|{a.step}"
    sigp = os.path.join(OUT, "run_signature.txt")
    if os.path.exists(sigp) and open(sigp).read() != sig:
        for p in (donep, tradesp, basep):
            if os.path.exists(p):
                os.remove(p)                     # settings changed: start over
    open(sigp, "w").write(sig)
    done = set(open(donep).read().split()) if os.path.exists(donep) else set()
    base = json.load(open(basep)) if os.path.exists(basep) else {}
    todo = [t for t in tickers if t not in done and os.path.exists(_path(t))]
    print(f"{len(todo)} tickers to process ({len(done)} already done)")
    spw, gates = build_gates(a.start, a.step)
    print(f"{len(gates)} sampled Fridays from {min(gates).date()} to {max(gates).date()}")
    deadline = time.time() + a.time_budget * 60
    buf, fresh = [], []
    header = not os.path.exists(tradesp)

    def flush():
        nonlocal header, buf, fresh
        if buf:
            pd.DataFrame(buf).to_csv(tradesp, mode="a", header=header, index=False)
            header = False
        with open(donep, "a") as f:
            f.write("\n".join(fresh) + ("\n" if fresh else ""))
        json.dump(base, open(basep, "w"))
        buf, fresh = [], []

    t0 = time.time()
    with Pool(a.procs, initializer=_init, initargs=(spw, gates, smap, a.start, a.step)) as pool:
        for k, (t, tr, b) in enumerate(pool.imap_unordered(process, todo, chunksize=4), 1):
            buf += tr
            fresh.append(t)
            for key, (n_, p_) in b.items():
                cur = base.setdefault(key, [0, 0])
                cur[0] += n_
                cur[1] += p_
            if k % 40 == 0:
                flush()
                print(f"  {k}/{len(todo)} stocks, {time.time() - t0:.0f}s")
            if time.time() > deadline:
                print("time budget reached; progress saved, run again to continue")
                pool.terminate()
                break
    flush()
    cmd_report(a)
    cmd_portfolio(a)


# ---------------------------------------------------------------- report -----
def _rate(s):
    s = pd.to_numeric(s, errors="coerce").dropna()
    if not len(s):
        return {"n": 0, "win": None, "avg": None, "median": None}
    return {"n": int(len(s)), "win": round(float((s > 0).mean() * 100), 1),
            "avg": round(float(s.mean()), 1), "median": round(float(s.median()), 1)}


def cmd_report(a):
    tp = os.path.join(OUT, "trades.csv")
    if not os.path.exists(tp):
        print("no trades yet")
        return
    df = pd.read_csv(tp)
    base = json.load(open(os.path.join(OUT, "baseline.json"))) if os.path.exists(
        os.path.join(OUT, "baseline.json")) else {}
    done = len(open(os.path.join(OUT, "done.txt")).read().split())
    S = {"stocks_processed": done, "trades": int(len(df)),
         "first_signal": df["signal"].min(), "last_signal": df["signal"].max()}
    for style in ("investor", "trader"):
        closed = df[df[f"{style}_reason"] != "OPEN"]
        S[f"book_exit_{style}"] = _rate(closed[f"{style}_ret"])
        S[f"book_exit_{style}"]["still_open"] = int((df[f"{style}_reason"] == "OPEN").sum())
        S[f"book_exit_{style}"]["avg_weeks_held"] = round(float(closed[f"{style}_weeks"].mean()), 1) if len(closed) else None
        S[f"book_exit_{style}"]["exits"] = {k: int(v) for k, v in closed[f"{style}_reason"].str.replace(
            r" \(entry week\)", "", regex=True).value_counts().items()}
    for h in HORIZONS:
        S[f"hold_{h}w"] = _rate(df[f"ret{h}"])
        n_ = sum(v[0] for k, v in base.items() if k.endswith(f"|{h}"))
        p_ = sum(v[1] for k, v in base.items() if k.endswith(f"|{h}"))
        S[f"hold_{h}w"]["baseline_win"] = round(100 * p_ / n_, 1) if n_ else None
        S[f"hold_{h}w"]["baseline_n"] = int(n_)
    by_year, by_verdict = {}, {}
    df["year"] = df["signal"].str[:4]
    for y, g in df.groupby("year"):
        c = g[g["investor_reason"] != "OPEN"]
        by_year[y] = {"trades": int(len(g)), "book_exit_win": _rate(c["investor_ret"])["win"],
                      "hold_26w_win": _rate(g["ret26"])["win"],
                      "baseline_26w_win": (round(100 * base[f"{y}|26"][1] / base[f"{y}|26"][0], 1)
                                           if base.get(f"{y}|26", [0])[0] else None)}
    for v, g in df.groupby("verdict"):
        c = g[g["investor_reason"] != "OPEN"]
        by_verdict[v] = {"trades": int(len(g)), "book_exit": _rate(c["investor_ret"]),
                         "hold_26w": _rate(g["ret26"])}
    S["by_year"], S["by_verdict"] = by_year, by_verdict
    S["lowvol_flag_trades"] = int(df["lowvol_flag"].sum()) if "lowvol_flag" in df else 0
    S["sector_gate_unknown_trades"] = int((df["sector_stage"].astype(str) == "").sum())
    json.dump(S, open(os.path.join(OUT, "summary.json"), "w"), indent=1)
    write_md(S)
    print(json.dumps({k: v for k, v in S.items() if k not in ("by_year", "by_verdict")}, indent=1))


def _p(v):
    return "n/a" if v is None or v != v else f"{v}%"


def write_md(S):
    L = ["# Backtest: how often is an Active Buy positive?", "",
         f"{S['trades']:,} signals from {S['first_signal']} to {S['last_signal']}, "
         f"{S['stocks_processed']:,} stocks. Same code as the Friday screen, using only data available "
         "on each Friday. Entry at the next Monday's open.", "",
         "## Win rate = share of trades that ended positive", "",
         "| Exit rule | Trades | Win rate | Average | Median |", "|---|---|---|---|---|"]
    for k, lab in (("book_exit_investor", "Book exit, investor (stop / Stage 3-4 / failed breakout)"),
                   ("book_exit_trader", "Book exit, trader (adds 30-week MA exit)")):
        r = S[k]
        L.append(f"| {lab} | {r['n']:,} | **{r['win']}%** | {r['avg']}% | {r['median']}% |")
    for h in HORIZONS:
        r = S[f"hold_{h}w"]
        L.append(f"| Hold {h} weeks, no stop | {r['n']:,} | **{r['win']}%** | {r['avg']}% | {r['median']}% |")
    L += ["", "## Compared with buying any stock on the same Fridays (baseline)", "",
          "| Horizon | Active Buys positive | Any stock positive | Lift |", "|---|---|---|---|"]
    for h in HORIZONS:
        r = S[f"hold_{h}w"]
        if r["win"] is not None and r["baseline_win"] is not None:
            L.append(f"| {h} weeks | {r['win']}% | {r['baseline_win']}% (n={r['baseline_n']:,}) | "
                     f"{r['win'] - r['baseline_win']:+.1f} pts |")
    L += ["", "## By year of signal", "", "| Year | Trades | Book-exit win | 26-wk win | Any stock 26-wk |",
          "|---|---|---|---|---|"]
    for y, r in sorted(S["by_year"].items()):
        L.append(f"| {y} | {r['trades']} | {_p(r['book_exit_win'])} | {_p(r['hold_26w_win'])} | {_p(r['baseline_26w_win'])} |")
    L += ["", "## By buy type", "", "| Type | Trades | Book-exit win | 26-wk win |", "|---|---|---|---|"]
    for v, r in S["by_verdict"].items():
        L.append(f"| {v} | {r['trades']} | {r['book_exit']['win']}% | {r['hold_26w']['win']}% |")
    L += ["", f"Open positions (no sell signal yet): investor {S['book_exit_investor']['still_open']}, "
          f"trader {S['book_exit_trader']['still_open']}; excluded from the book-exit rows.", "",
          "## Read this before trusting the numbers", "",
          "- Survivorship bias: only stocks listed today are in the data. Delisted and bankrupt companies are "
          "missing, so every row reads better than reality.",
          "- Sector classification is today's; sector ETFs stand in for industry groups. "
          f"{S['sector_gate_unknown_trades']} trades had no sector ETF history and skipped the sector gate.",
          f"- {S['lowvol_flag_trades']} of {S['trades']} signals were, at some point after entry, flagged by the book's own "
          "sell logic as breakouts without enough volume (p.116: sell on the first rally). A buy that the sell side "
          "calls weak is a sign the buy and sell checks disagree; those trades exit at the first weekly close above "
          "the entry price (our reading of 'first rally'), at the next open.",
          "- Our own choices, not the book's: entry at the next open, one position per stock, 'sell half' treated "
          "as sell all, 'take partial' ignored.",
          "- No news, no contrary-opinion or price/dividend gauges, no commissions or slippage.",
          "- A high win rate is not a high return: this counts positive trades, not how much they made."]
    open(os.path.join(OUT, "backtest.md"), "w").write("\n".join(L) + "\n")

# ------------------------------------------------------------- portfolio -----
def simulate(trades, spy_weekly, slots=15, start_equity=100_000.0, style="investor"):
    """Follow the backtest rules strictly as one account.

    Every position is 1/`slots` of the account's value on its entry day (the 1/15 sizing is
    ours, see CFG). Signals are taken in order of entry date; on the same day the youngest
    Stage 2 (fewest stage_weeks) comes first, as the screen orders them. When all slots are
    full the signal is skipped. Cash earns nothing. The account is valued every Friday at
    that week's close; entries and exits happen at the week's open.
    Returns (equity Series indexed by Friday, stats dict)."""
    tr = trades.copy()
    tr["entry_d"] = pd.to_datetime(tr["entry"])
    tr["exit_d"] = pd.to_datetime(tr[f"{style}_exit"])
    tr = tr.sort_values(["entry_d", "stage_weeks", "ticker"])
    closes = {}
    for t in tr["ticker"].unique():
        d = load(t)
        if d is None:
            continue
        d = d[~d.index.duplicated()].sort_index()
        closes[t] = weekly_of(d)["Close"]
    weeks = spy_weekly.index[spy_weekly.index >= tr["entry_d"].min()]
    spo, spc = spy_weekly["Open"], spy_weekly["Close"]

    def spy_open(d):
        x = spo.loc[:d]
        return float(x.iloc[-1]) if len(x) else float(spo.iloc[0])

    cash_b = start_equity                # same dollars, same dates, but in SPY
    cash = start_equity
    open_pos = []                         # dicts: t, shares, exit_d, exit_px
    taken = skipped = 0
    eq, eq_b, invested = [], [], []
    by_entry = {k: g for k, g in tr.groupby("entry_d")}
    pending_exit = []

    def value(w):
        v = 0.0
        for p in open_pos:
            c = closes.get(p["t"])
            px = None
            if c is not None:
                px = c.loc[:w].iloc[-1] if len(c.loc[:w]) else None
            v += p["shares"] * (px if px is not None else p["entry_px"])
        return v

    def settle(w):
        nonlocal cash, cash_b
        for p in [p for p in open_pos if p["exit_d"] is not None and p["exit_d"] <= w]:
            cash += p["shares"] * p["exit_px"]
            cash_b += p["spy_shares"] * spy_open(p["exit_d"])
            open_pos.remove(p)

    for w in weeks:
        settle(w)                                         # exits at this week's open (earlier entries)
        for _, r in (by_entry[w].iterrows() if w in by_entry else []):
            if len(open_pos) >= slots or r["ticker"] in {p["t"] for p in open_pos}:
                skipped += 1
                continue
            equity_now = cash + value(w - pd.Timedelta(days=7))
            size = min(equity_now / slots, cash)
            if size <= 0:
                skipped += 1
                continue
            ret = r[f"{style}_ret"] / 100.0
            still_open = r[f"{style}_reason"] == "OPEN"
            open_pos.append({"t": r["ticker"], "shares": size / r["entry_px"], "entry_px": r["entry_px"],
                             "spy_shares": size / spy_open(w),
                             "exit_d": None if still_open else r["exit_d"],
                             "exit_px": r["entry_px"] * (1 + ret)})
            cash -= size
            cash_b -= size
            taken += 1
        settle(w)                                         # same-week exits (stop hit in the entry week)
        v = value(w)
        eq_b.append(cash_b + sum(p["spy_shares"] for p in open_pos) * float(spc.loc[:w].iloc[-1]))
        eq.append(cash + v)
        invested.append(v / (cash + v) if cash + v else 0)
    eq = pd.Series(eq, index=weeks)
    eq_b = pd.Series(eq_b, index=weeks)
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    dd = (eq / eq.cummax() - 1).min()
    st = {"start": str(eq.index[0].date()), "end": str(eq.index[-1].date()), "years": round(yrs, 1),
          "final_multiple": round(float(eq.iloc[-1] / start_equity), 2),
          "cagr": round(float((eq.iloc[-1] / start_equity) ** (1 / yrs) - 1) * 100, 1),
          "max_drawdown": round(float(dd) * 100, 1),
          "signals_taken": taken, "signals_skipped_full": skipped,
          "avg_invested_pct": round(float(np.mean(invested)) * 100, 1),
          "matched_spy_final_multiple": round(float(eq_b.iloc[-1] / start_equity), 2),
          "matched_spy_cagr": round(float((eq_b.iloc[-1] / start_equity) ** (1 / yrs) - 1) * 100, 1),
          "matched_spy_max_drawdown": round(float((eq_b / eq_b.cummax() - 1).min()) * 100, 1)}
    return eq, st, eq_b


def cmd_portfolio(a, slots=15):
    tp = os.path.join(OUT, "trades.csv")
    spy = load("SPY")
    if not os.path.exists(tp) or spy is None:
        print("portfolio: need trades.csv and SPY history (run fetch)")
        return
    trades = pd.read_csv(tp)
    spw = weekly_of(spy)
    eq, st, eq_b = simulate(trades, spw, slots)
    base = spw["Close"].loc[eq.index]
    spy_eq = base / base.iloc[0] * 100_000.0
    yrs = st["years"]
    st["spy_final_multiple"] = round(float(spy_eq.iloc[-1] / 100_000.0), 2)
    st["spy_cagr"] = round(float((spy_eq.iloc[-1] / 100_000.0) ** (1 / yrs) - 1) * 100, 1)
    st["spy_max_drawdown"] = round(float((spy_eq / spy_eq.cummax() - 1).min()) * 100, 1)
    ann = {}
    for y in sorted(set(eq.index.year)):
        e_, s_ = eq[eq.index.year == y], spy_eq[spy_eq.index.year == y]
        prev_e = eq[eq.index.year == y - 1]
        prev_s = spy_eq[spy_eq.index.year == y - 1]
        e0 = prev_e.iloc[-1] if len(prev_e) else e_.iloc[0]
        s0 = prev_s.iloc[-1] if len(prev_s) else s_.iloc[0]
        b_ = eq_b[eq_b.index.year == y]
        pb = eq_b[eq_b.index.year == y - 1]
        b0 = pb.iloc[-1] if len(pb) else b_.iloc[0]
        ann[str(y)] = {"strategy": round(float(e_.iloc[-1] / e0 - 1) * 100, 1),
                       "matched": round(float(b_.iloc[-1] / b0 - 1) * 100, 1),
                       "spy": round(float(s_.iloc[-1] / s0 - 1) * 100, 1)}
    st["by_year"] = ann
    json.dump(st, open(os.path.join(OUT, "portfolio.json"), "w"), indent=1)
    L = ["", f"## One account following the rules strictly vs SPY ({st['start']} to {st['end']})", "",
         f"Start $100,000. Each position is 1/{slots} of the account at entry; signals taken in order, skipped when "
         f"all {slots} slots are full; cash earns nothing; investor book exit; no commissions, taxes or slippage.",
         "", "Two SPY comparisons (SPY is total return, dividends included):",
         "- **Matched SPY:** every time the strategy buys $X of a stock, the same $X goes into SPY that week; "
         "when the stock is sold, that SPY is sold the same week. Same dollars, same dates, same idle cash, so the "
         "only difference is stock picking and exits.",
         "- **Buy and hold SPY:** fully invested from the first day.", "",
         "| | Strategy | Matched SPY | Buy and hold SPY |", "|---|---|---|---|",
         f"| Ending value | ${100000 * st['final_multiple']:,.0f} ({st['final_multiple']}x) | "
         f"${100000 * st['matched_spy_final_multiple']:,.0f} ({st['matched_spy_final_multiple']}x) | "
         f"${100000 * st['spy_final_multiple']:,.0f} ({st['spy_final_multiple']}x) |",
         f"| Per year (CAGR) | {st['cagr']}% | {st['matched_spy_cagr']}% | {st['spy_cagr']}% |",
         f"| Worst drop (weekly closes) | {st['max_drawdown']}% | {st['matched_spy_max_drawdown']}% | {st['spy_max_drawdown']}% |",
         f"| Average share of account invested | {st['avg_invested_pct']}% | {st['avg_invested_pct']}% | 100% |",
         f"| Signals taken / skipped (slots full) | {st['signals_taken']:,} / {st['signals_skipped_full']:,} | | |",
         "", "| Year | Strategy | Matched SPY | Buy and hold SPY |", "|---|---|---|---|"]
    for y, r in ann.items():
        L.append(f"| {y} | {r['strategy']}% | {r['matched']}% | {r['spy']}% |")
    L += ["", "Survivorship bias flatters the strategy side (delisted stocks are missing), so treat any "
          "edge over SPY as an upper bound."]
    mdp = os.path.join(OUT, "backtest.md")
    txt = open(mdp).read()
    cut = txt.find("\n## One account following")
    if cut >= 0:
        txt = txt[:cut]
    open(mdp, "w").write(txt.rstrip("\n") + "\n" + "\n".join(L) + "\n")
    print(json.dumps({k: v for k, v in st.items() if k != "by_year"}, indent=1))



def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--universe", choices=["all", "cache"], default="all")
    f.add_argument("--time-budget", type=float, default=300)
    f.add_argument("--rebuild-universe", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--start", default="2000-01-01")
    r.add_argument("--step", type=int, default=1, help="1 = every Friday, 2 = every other, ...")
    r.add_argument("--procs", type=int, default=os.cpu_count() or 2)
    r.add_argument("--time-budget", type=float, default=300)
    r.add_argument("--max-tickers", type=int, default=0)
    sub.add_parser("report")
    sub.add_parser("portfolio")
    a = p.parse_args()
    {"fetch": cmd_fetch, "run": cmd_run, "report": cmd_report, "portfolio": cmd_portfolio}[a.cmd](a)


if __name__ == "__main__":
    main()
