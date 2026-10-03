"""Test a short, fixed list of book-grounded variants against SPY, with the history split in three.

    python variants.py            (after `backtest.py run` has produced data/backtest/trades.csv)

The list below is written down BEFORE looking at results. Every variant cites the book (or says
plainly that it is not from the book). Nothing here changes the live screener.

HISTORY SPLIT (to guard against fitting the past):
  development 2000-2012   : look at this freely
  validation  2013-2019   : a variant must also help here
  holdout     2020-2026   : looked at once, at the end; the hardest test (the base strategy is weakest here)

A variant only counts as an improvement if it beats the base in development AND validation, and
is not worse in holdout. Each period is compared with buy-and-hold SPY (total return).
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as b

PERIODS = {"development": ("2000-01-01", "2012-12-31"),
           "validation": ("2013-01-01", "2019-12-31"),
           "holdout": ("2020-01-01", "2026-12-31")}

# name -> (what it changes, book basis, filter(df)->df, simulate kwargs)
V = {}


def add(name, why, basis, flt=None, **kw):
    V[name] = (why, basis, flt or (lambda d: d), kw)


add("BASE", "As backtested: youngest Stage 2 first, 15 slots, investor sells half at Stage 3", "p.36-37")
add("RANK_TRIPLE", "Take the best triple-confirmation score first (volume, RS, group)",
    "p.150-152, p.157 (all three signals together)", order=(("triple_score", False), ("stage_weeks", True)))
add("RANK_RS", "Take the strongest relative strength first",
    "p.110-113 (the stronger the RS the better)", order=(("rs", False), ("stage_weeks", True)))
add("RANK_VOL", "Take the heaviest breakout volume first",
    "p.150 (volume is the confirmation), p.59", order=(("bo_vol_ratio", False), ("stage_weeks", True)))
add("NO_PULLBACKS", "Skip pullback buys (the weakest type in the first backtest)",
    "p.150 (buy the breakout); pullbacks are the secondary buy, p.105/115",
    flt=lambda d: d[d["verdict"] != "PULLBACK - BUY"])
add("EARLY_ONLY", "Only Stage 2 runs 26 weeks old or less",
    "p.99, p.129 (early Stage 2 is the ideal; late is chasing)",
    flt=lambda d: d[d["stage_weeks"] <= 26])
add("SECTOR_S2", "Only when the sector itself is in Stage 2 (not Stage 1)",
    "p.80 (best: a group with the same pattern; healthy = not Stage 3 or 4)",
    flt=lambda d: d[pd.to_numeric(d["sector_stage"], errors="coerce") == 2])
add("SLOTS_10", "10 positions instead of 15", "not from the book (sizing is ours)", slots=10)
add("SLOTS_20", "20 positions instead of 15", "not from the book (sizing is ours)", slots=20)
add("FULL_AT_S3", "Investor sells all at Stage 3 (the earlier backtest)", "p.36 is for traders; investors sell half p.36-37",
    full3=True)
add("IDLE_IN_SPY", "Idle cash held in SPY", "NOT from the book: a portfolio overlay", idle_spy=True)
add("BOOK_QUALITY", "RANK_TRIPLE + no pullbacks + early only (declared in advance, not picked from results)",
    "combination of the three above",
    flt=lambda d: d[(d["verdict"] != "PULLBACK - BUY") & (d["stage_weeks"] <= 26)],
    order=(("triple_score", False), ("stage_weeks", True)))


def stats_for(eq, spy_eq, lo, hi):
    e, s = eq.loc[lo:hi], spy_eq.loc[lo:hi]
    if len(e) < 20:
        return None
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    cg = lambda x: (x.iloc[-1] / x.iloc[0]) ** (1 / yrs) - 1
    yr_e, yr_s = e.groupby(e.index.year).last(), s.groupby(s.index.year).last()
    ye = yr_e.pct_change().dropna()
    ys = yr_s.pct_change().dropna()
    return {"cagr": round(cg(e) * 100, 1), "spy_cagr": round(cg(s) * 100, 1),
            "diff": round((cg(e) - cg(s)) * 100, 1),
            "max_dd": round(float((e / e.cummax() - 1).min()) * 100, 1),
            "spy_max_dd": round(float((s / s.cummax() - 1).min()) * 100, 1),
            "years_beating_spy": f"{int((ye > ys).sum())}/{len(ye)}"}


MIN_TRADES = int(os.environ.get("MIN_TRADES", 50))


def main():
    tp = os.path.join(b.OUT, "trades.csv")
    trades = pd.read_csv(tp)
    need = {"triple_score", "rs", "bo_vol_ratio", "investor_exit1"}
    if not need <= set(trades.columns):
        raise SystemExit("trades.csv lacks the signal columns; rerun `backtest.py run` (v2)")
    spy = b.load("SPY")
    spw = b.weekly_of(spy)
    closes = b.load_closes(trades["ticker"].unique())
    out = {}
    for name, (why, basis, flt, kw) in V.items():
        sub = flt(trades)
        if len(sub) < MIN_TRADES:
            out[name] = {"why": why, "basis": basis, "trades": int(len(sub)), "skipped": "too few trades"}
            continue
        eq, st, eqb = b.simulate(sub, spw, closes=closes, **kw)
        # SPY buy-and-hold on the same weekly grid
        spy_eq = spw["Close"].loc[eq.index]
        spy_eq = spy_eq / spy_eq.iloc[0] * eq.iloc[0]
        res = {"why": why, "basis": basis, "signals": int(len(sub)), "taken": st["signals_taken"],
               "invested_pct": st["avg_invested_pct"], "all": stats_for(eq, spy_eq, "1900", "2100")}
        for p, (lo, hi) in PERIODS.items():
            res[p] = stats_for(eq, spy_eq, lo, hi)
        out[name] = res
    json.dump(out, open(os.path.join(b.OUT, "variants.json"), "w"), indent=1)
    write_md(out)
    print(open(os.path.join(b.OUT, "variants.md")).read())


def write_md(out):
    base = out["BASE"]
    L = ["# Variants vs SPY", "",
         "Each row: strategy return per year minus buy-and-hold SPY (total return) over the same period. "
         "Positive = beat SPY. Development 2000-2012, validation 2013-2019, holdout 2020-2026.", "",
         "| Variant | Dev | Valid | Holdout | All years | Worst drop (SPY) | Years beating SPY | Taken |",
         "|---|---|---|---|---|---|---|---|"]
    for name, r in out.items():
        if "skipped" in r:
            L.append(f"| {name} | too few trades | | | | | | |")
            continue
        f = lambda k: ("n/a" if r[k] is None else f"{r[k]['diff']:+.1f}")
        a = r["all"]
        L.append(f"| {name} | {f('development')} | {f('validation')} | {f('holdout')} | {a['diff']:+.1f} "
                 f"({a['cagr']}% vs {a['spy_cagr']}%) | {a['max_dd']}% ({a['spy_max_dd']}%) | "
                 f"{a['years_beating_spy']} | {r['taken']} |")
    L += ["", "## Did anything improve on the base?", ""]
    bd = lambda r, k: r[k]["diff"] if r.get(k) else None
    for name, r in out.items():
        if name == "BASE" or "skipped" in r:
            continue
        d, v, h = (bd(r, k) for k in ("development", "validation", "holdout"))
        bd0, bv0, bh0 = (bd(base, k) for k in ("development", "validation", "holdout"))
        if None in (d, v, h, bd0, bv0, bh0):
            continue
        ok = d > bd0 and v > bv0 and h >= bh0
        L.append(f"- **{name}**: {'improves on the base in all three periods' if ok else 'does not pass (needs better in development and validation, and no worse in holdout)'} "
                 f"(dev {d - bd0:+.1f}, valid {v - bv0:+.1f}, holdout {h - bh0:+.1f} pts per year vs base). Basis: {r['basis']}.")
    L += ["", "## Variants tested", ""]
    for name, r in out.items():
        L.append(f"- **{name}**: {r['why']}. Basis: {r['basis']}.")
    L += ["", "Read with care: eleven variants were tried, so one will look good by luck; only a variant that passes "
          "development and validation AND holdout, and has a basis in the book, is worth a conversation. "
          "Survivorship bias still flatters every row, and there are no costs or taxes."]
    open(os.path.join(b.OUT, "variants.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
