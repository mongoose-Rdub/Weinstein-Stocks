#!/usr/bin/env python3
"""
WEINSTEIN PURE
==============
A screener built ONLY from Stan Weinstein's "Secrets for Profiting in Bull and
Bear Markets" (1988). No Minervini Trend Template, no VCP, no ATR stops, no
RSI, no extension ceilings -- none of that is in the book.

It follows the Quick Reference Guide on Buying (p.115):

    * Check the major trend of the overall market.
    * Uncover the few groups that look best technically.
    * Make a list of those stocks in the favorable groups that have bullish
      patterns but are now in trading ranges. Write down the price that each
      would need to break out.
    * Narrow down the list. Discard those that have overhead resistance nearby.
    * Narrow the list further by checking relative strength.
    * Put in your buy-stop orders for half of your position...
    * If volume is favorable on the breakout and contracts on the decline, buy
      your other half position on a pullback toward the initial breakout.
    * If the volume pattern is negative (not high enough on breakout), sell the
      stock on the first rally...

So it produces TWO lists:

    1. BUY-STOP WATCHLIST -- stocks coiled in a trading range inside a
       favorable group, with the exact price that triggers the buy.
    2. ACTIVE BUYS -- breakouts, continuation breakouts and pullbacks that
       already satisfy the volume and relative-strength tests.

Usage:
    python3 weinstein_pure.py
    python3 weinstein_pure.py --universe sp500
    python3 weinstein_pure.py --tickers AMZN,RNG,TDY --detail

Data plumbing (download, caching, throttling, universe lists) is imported from
stage_vcp_screener so those solved problems are not re-solved here. Every line
of METHOD below is derived from the book, with page references.
"""

import argparse
import math
import sys

import numpy as np
import pandas as pd

import stage_vcp_screener as infra


# ----------------------------------------------------------------------------
# METHOD SETTINGS -- every one traceable to the book
# ----------------------------------------------------------------------------

CFG = {
    # "a 30-week moving average (MA) is the best one for long-term investors"
    # (p.13). The book's own instructions for calculating it are a SIMPLE
    # average: add the 30 weeks, divide by 30 (p.313). Mansfield's charts plot
    # a weighted one (p.25), but the method as Weinstein teaches it to the
    # reader is the simple average.
    "ma_length": 30,
    "ma_type": "SMA",
    "slope_lookback": 2,
    # "must no longer be declining" (p.14) -- flat qualifies, so a small
    # negative slope inside this band still counts as non-declining.
    "ma_flat_tol": 0.002,

    # "volume of even more than twice the average trading of the past four
    # weeks" (p.150); the Goodyear breakout was measured against "the average
    # weekly volume for the prior four weeks" (p.105).
    "vol_base_weeks": 4,
    "breakout_vol_mult": 2.0,
    # Second form of the volume test (p.104): "a volume build-up over the past
    # three to four weeks that is at least twice the average volume of the past
    # several weeks, coupled with at least some increase on the breakout week".
    "buildup_weeks": 3,
    "buildup_base_weeks": 4,
    # Triple-confirmation pattern (p.150-152). The book: volume "more than
    # twice" the prior four weeks with "several more weeks of heavy trading"
    # after; RS "in negative territory or hugging the zero line" then moving
    # "decisively into positive territory"; a prior rise of "some 40 to 50
    # percent or more". Only the 2x and 40% are the book's numbers; the
    # follow-through multiple and the zero-line band are ours.
    "triple_follow_mult": 1.5,
    "triple_rs_before_max": 3.0,
    "triple_prior_advance_pct": 40.0,
    # On the pullback, "volume contracted by over 75 percent from peak levels"
    # (p.105): the pullback week must be at or below 25% of the breakout's peak
    # week. (pullback_vol_max is only a fallback when no peak can be measured.)
    "pullback_vol_max": 0.75,
    "pullback_vol_peak_max": 0.25,
    "breakout_peak_weeks": 4,    # weeks from Stage 2 entry that set the "peak"

    # Trading-range (Stage 1 base) detection. "This basing action can go on for
    # months or, in some cases, years" (p.33).
    "base_window": 30,          # weeks examined for the range
    "base_min_weeks": 8,        # a range must have lasted at least this long
    "base_max_width": 0.40,     # high/low spread that still counts as a range

    # "Discard those that have overhead resistance nearby" (p.115). Old
    # resistance is weaker: one level was "close to two years old, which made
    # it far less potent" (p.119).
    "resistance_scan_years": 5,
    # "Nearby": the book's own example has a breakout at 15 with "a clear zone
    # of resistance just a few points overhead" starting at 18 (p.98) -- 20%.
    "resistance_near_pct": 20.0,
    # Resistance "close to two years old... far less potent" (p.120), and
    # "2-plus years" old is "no big deal" (p.98). "Close to" two years is read
    # as within about 10% of 104 weeks.
    "resistance_stale_weeks": 94,
    # Repeated tops: Pan Am "topped out above 8 in each of the four years"
    # (p.110). A level turned back again and again is heavy supply even if the
    # stock never spent long up there. The book gives no count or tolerance;
    # three swing highs within 3% of each other is our reading.
    "resistance_min_tests": 3,
    "resistance_cluster_tol": 0.03,
    "advance_lookback_weeks": 104,  # how far back to look for an earlier Stage 2 advance
    "pivot_k": 3,                  # swing-high definition (+/- k weeks)

    # Relative strength vs the S&P. Mansfield's RS has a zero line; above is a
    # long-term positive (p.110). Sub-zero is still buyable if "in good shape
    # and improving" (p.110), but not if "deep in negative territory" and
    # trending down (p.113).
    "rs_length": 52,
    "rs_trend_weeks": 13,        # "trending higher for 90 days" (p.111)
    "rs_deep_negative": 10.0,

    # The pullback buy is a dip "back close to the breakout point" (p.34).
    "pullback_band": 8.0,        # how far above the breakout price still counts
    "pullback_below": 3.0,       # tolerance for dipping under it

    # A breakout is "fresh" for a couple of weeks; after that a new move must
    # clear a fresh consolidation to count as a continuation buy (p.62).
    "fresh_weeks": 2,
    "continuation_lookback": 8,
    # Continuation buy: "after a Stage 2 advance is well underway, when the
    # stock drops back close to its MA and consolidates. It then breaks out
    # anew above the top of its resistance zone" (p.71). The book gives no
    # number for "close to"; 8% is our reading of it.
    "consol_near_ma_pct": 8.0,

    # "No matter how bullish a stock is, don't buy it too late in an advance,
    # when it is far above the ideal entry point" (p.139). "If you miss it at
    # 12 1/8, buying at 12 7/8 is no big deal, but paying 25 or 26 sure is!"
    # (p.46). The book gives no exact cutoff; 10% above the entry point is our
    # reading of "far above". About 80% of initial breakouts pull back (p.72),
    # so a stock past this line goes to WAIT, not AVOID.
    "max_chase_pct": 10.0,

    # "strict" -> nearby resistance discards the stock (p.115, p.139 literally);
    #             discarded candidates are still listed in their own table.
    # "flag"   -> nearby resistance is only shown in the RESISTANCE column.
    "resistance_mode": "strict",

    # The initial stop goes "right below the significant floor of support"
    # (p.183) -- the base floor -- one eighth under it, and under a round
    # number or half if it would land just above one (p.183).
    "stop_tick": 0.125,
    # A pullback buy is the second half of a position opened at the breakout
    # (p.34, p.115), so it carries that position's TRAILED stop: raised after
    # the first correction of "at least 8 to 10 percent" (p.194).
    "stop_correction_pct": 0.08,

    # "for a small portfolio ($10,000 to $25,000), I'd diversify into no more
    # than five or six stocks... 10 to 20 stocks are the most that I'd invest
    # in at any one time. Also, use approximately equal dollar amounts" (p.138).
    "account_size": 300_000,
    "positions": 15,

    # Liquidity: Weinstein gives NO numeric rule. He warns qualitatively about
    # execution quality (p.65n). This floor exists only so the screen does not
    # surface names you cannot trade; set to 0 to disable it entirely.
    "min_dollar_volume": 5_000_000,

    # "try to limit your purchases to those cases where the initial stop isn't
    # greater than 15 percent below your purchase price" (p.184), with
    # "occasional exceptions because a chart pattern is so outstanding". So a
    # wider stop is flagged, not dropped.
    "wide_stop_pct": 15.0,
    "range_scan_weeks": 260,
    "ceiling_scan_weeks": 78,    # how far before Stage 2 to look for a range ceiling     # how far back to measure real base length
    "min_price": 5.0,
}

BUY_VERDICTS = {"BREAKOUT - BUY", "CONTINUATION - BUY", "PULLBACK - BUY"}
WAIT_VERDICT = "WAIT - ABOVE ENTRY POINT"
NO_BASE_VERDICT = "NO BASE - SKIP"


def book_stop(level: float, tick: float = 0.125) -> float:
    """A sell-stop placed the way the book places it (p.183).

    One eighth below the floor ("18 1/8 -> 17 7/8" for an 18 1/4 bottom). And if
    that lands on or just above a round number or a half, go under it instead:
    "if the stop should be 18 1/8 or 18, enter it at 17 7/8 ... if 18 5/8, place
    it below the half at 18 3/8."
    """
    s = level - tick
    n = math.floor(s * 2) / 2
    if s - n <= tick + 1e-9:
        s = n - tick
    return s


# ----------------------------------------------------------------------------
# STAGE ENGINE -- the four stages exactly as described in Chapter 2
# ----------------------------------------------------------------------------

def classify(weekly: pd.DataFrame, ma: pd.Series, cfg: dict):
    """Weinstein's four stages, as a state machine.

    Stage 1 Basing      "the 30-week MA loses its downside slope and starts to
                         flatten out... rallies and declines will toss the
                         stock above and below the MA" (p.33)
    Stage 2 Advancing   entered on a "breakout above the top of the resistance
                         zone and the 30-week MA... on impressive volume"
                         (p.33); held while "all downside corrections were
                         contained above the rising 30-week MA" (p.34)
    Stage 3 Top         "the 30-week MA loses its upward slope and starts to
                         flatten out. Whereas Stage 2 price declines always
                         held at or above the MA, the stock will now tiptoe
                         below and above the MA" (p.36)
    Stage 4 Declining   price below a declining MA, having broken support (p.38)

    Returns (stages, breakout_level) -- the level cleared on entry to Stage 2 is
    carried forward, because the pullback buy is measured against it.
    """
    close, high = weekly["Close"], weekly["High"]
    lb = cfg["slope_lookback"]

    prev = ma.shift(lb)
    slope = (ma - prev) / prev.abs().replace(0, np.nan)
    rising = (slope > cfg["ma_flat_tol"]).fillna(False).to_numpy()
    not_declining = (slope >= -cfg["ma_flat_tol"]).fillna(False).to_numpy()
    above = (close > ma).to_numpy()

    # the top of the trading range that a breakout must clear
    base_high = high.rolling(cfg["base_window"]).max().shift(1).to_numpy()

    c = close.to_numpy()
    ma_v = ma.to_numpy()
    n = len(close)
    out = np.full(n, np.nan)
    bo = np.full(n, np.nan)
    state = prev_state = None

    for i in range(n):
        if np.isnan(ma_v[i]) or np.isnan(slope.iloc[i]):
            continue            # need a measurable MA slope before judging
        broke_out = (not np.isnan(base_high[i])) and c[i] > base_high[i]

        if state is None:
            state = 2 if (above[i] and rising[i]) else (
                4 if (not above[i] and not rising[i]) else (1 if above[i] else 3))
        elif state == 2:
            # Stage 2 survives while corrections hold at or above the MA.
            if not above[i]:
                state = 4 if not not_declining[i] else 3
            elif not not_declining[i]:
                state = 3            # MA rolled over while price hangs on
        elif state == 3:
            if not above[i] and not not_declining[i]:
                state = 4
            elif above[i] and not_declining[i] and broke_out:
                state = 2            # a fresh breakout revives the advance
        elif state == 4:
            if above[i] and not_declining[i] and broke_out:
                state = 2
            elif above[i] or not_declining[i]:
                state = 1            # decline is over, basing begins
        elif state == 1:
            if above[i] and not_declining[i] and broke_out:
                state = 2
            elif not above[i] and not not_declining[i]:
                state = 4

        if state == 2:
            bo[i] = base_high[i] if prev_state != 2 else (bo[i - 1] if i else np.nan)
        prev_state = state
        out[i] = state

    return pd.Series(out, index=close.index), pd.Series(bo, index=close.index)


# ----------------------------------------------------------------------------
# TRADING RANGE + OVERHEAD RESISTANCE
# ----------------------------------------------------------------------------

def trading_range(weekly: pd.DataFrame, ma: pd.Series, cfg: dict):
    """Is the stock coiled in a trading range, and what price breaks it out?

    "Make a list of those stocks in the favorable groups that have bullish
    patterns but are now in trading ranges. Write down the price that each
    would need to break out." (p.115)
    """
    w = cfg["base_window"]
    if len(weekly) < w + cfg["slope_lookback"] + 1:
        return None

    seg = weekly.iloc[-w:]
    top = float(seg["High"].max())
    bottom = float(seg["Low"].min())
    if bottom <= 0:
        return None

    width = (top - bottom) / bottom
    price = float(weekly["Close"].iloc[-1])

    # how long price has actually been confined to the band -- measured over
    # the full history, not just the 30-week window, since long bases are the
    # good ones ("months or, in some cases, years", p.33)
    hist = weekly.iloc[-cfg["range_scan_weeks"]:]
    weeks_in = 0
    for i in range(len(hist) - 1, -1, -1):
        if bottom <= hist["Low"].iloc[i] and hist["High"].iloc[i] <= top * 1.001:
            weeks_in += 1
        else:
            break

    prev = ma.shift(cfg["slope_lookback"])
    slope = float(((ma - prev) / prev.abs()).iloc[-1])

    return {
        "range_top": round(top, 2),
        "range_bottom": round(bottom, 2),
        "range_width_pct": round(width * 100, 1),
        "range_weeks": weeks_in,
        # a base is valid when it is tight enough, long enough, and the MA has
        # stopped falling -- "the MA no longer declining sharply but has
        # instead started to flatten out" (p.63)
        "is_range": bool(width <= cfg["base_max_width"]
                         and weeks_in >= cfg["base_min_weeks"]
                         and slope >= -cfg["ma_flat_tol"]),
        # the buy-stop goes just above the range top (p.64)
        "buy_stop": round(top * 1.002, 2),
        "pct_to_trigger": round((top - price) / price * 100, 1),
    }


def pivot_highs(weekly: pd.DataFrame, k: int):
    """Swing highs: a bar whose high exceeds the k bars either side."""
    h = weekly["High"].to_numpy()
    idx = []
    for i in range(k, len(h) - k):
        if h[i] == max(h[i - k:i + k + 1]):
            idx.append(i)
    # a peak made in the last k weeks cannot yet show k bars on its right, but
    # it is still supply if price has since fallen away from it
    for i in range(max(k, len(h) - k), len(h) - 1):
        if h[i] == max(h[i - k:]):
            idx.append(i)
    return idx


def range_before(weekly, start, max_width):
    """The trading range the stock was in just before index `start`.

    Walks back from the week before the breakout for as long as the high/low
    spread stays within max_width. Returns (weeks, floor, top). Stage labels
    flicker between 4 and 1 while a base is forming (the 30-week MA is still
    sloping down as price chops around it), so the range itself is the more
    reliable evidence of a base -- "moving sideways in a trading range" (p.33).
    """
    hi = lo = None
    n = 0
    for i in range(start - 1, -1, -1):
        h, l = float(weekly["High"].iloc[i]), float(weekly["Low"].iloc[i])
        nh = h if hi is None else max(hi, h)
        nl = l if lo is None else min(lo, l)
        if nl <= 0 or (nh - nl) / nl > max_width:
            break
        hi, lo, n = nh, nl, n + 1
    return n, lo, hi


def basing_run(stv, dur, min_weeks):
    """How long the stock was basing before the current Stage 2 began.

    Returns (base_start_index, weeks, prior_stage), where prior_stage is the
    stage that came before the base (2 = the stock was already advancing, 4 =
    it came off a decline, 0 = no history). Basing weeks are Stage 1, or Stage 3 for
    a stock that topped and re-based. A brief Stage 2 inside the range is a
    false breakout, not an advance, so it does not end the base; a Stage 2 run
    of min_weeks or more is a real advance and does. A Stage 4 week ends it.
    """
    start = len(stv) - dur
    base_start, j = start, start - 1
    while j >= 0:
        v = stv[j]
        if v in (1, 3):
            base_start, j = j, j - 1
            continue
        if v == 2:
            k = j
            while k >= 0 and stv[k] == 2:
                k -= 1
            if j - k < min_weeks:
                base_start, j = k + 1, k
                continue
        break
    prior = int(stv[j]) if 0 <= j < len(stv) and not np.isnan(stv[j]) else 0
    return base_start, start - base_start, prior


def trailed_correction_low(weekly, start, correction_pct, recover_frac=0.5):
    """Low of the latest COMPLETED correction since Stage 2 began (p.194).

    "After the first substantial correction of at least 8 to 10 percent, you
    are now going to get set to raise the stop. But you don't actually change
    it until your stock ends its correction and moves back toward its prior
    high." A correction is a drop of correction_pct or more from the running
    high; it is complete once a weekly close has recovered recover_frac of the
    way back to that high (the book gives no figure for "back toward"; half is
    our reading). Returns None if there has been no completed correction.
    """
    H = weekly["High"].to_numpy()
    L = weekly["Low"].to_numpy()
    C = weekly["Close"].to_numpy()
    peak, best = -np.inf, None
    in_corr, trough, corr_peak = False, None, None
    for i in range(start, len(H)):
        if not in_corr:
            peak = max(peak, H[i])
            if L[i] <= peak * (1 - correction_pct):
                in_corr, trough, corr_peak = True, L[i], peak
        else:
            trough = min(trough, L[i])
            if C[i] >= trough + (corr_peak - trough) * recover_frac:
                best, in_corr = trough, False
                peak = max(corr_peak, H[i])
    return best


def cleared_ceiling(weekly, bo_level, price, dur, cfg):
    """The real top of the range, when it is older than the 30-week window.

    The breakout level is the highest high of the prior 30 weeks. A base can
    run longer than that, and the ceiling that defines it (the peak before the
    base formed) may sit just outside the window. If price has now cleared
    such a peak, THAT is the level it broke out through ("above the top of the
    resistance zone", p.33), so the pullback buy should be measured from it.
    Only swing highs made before this Stage 2 began are considered.
    """
    k = cfg["pivot_k"]
    start = len(weekly) - dur
    seg = weekly.iloc[max(0, start - cfg["ceiling_scan_weeks"]):start]
    if len(seg) <= 2 * k + 1:
        return bo_level
    peaks = [float(seg["High"].iloc[i]) for i in pivot_highs(seg, k)]
    cleared = [h for h in peaks if bo_level * 1.001 < h < price]
    return max(cleared) if cleared else bo_level


def overhead_resistance(weekly: pd.DataFrame, cfg: dict,
                        ref_price: float = None, floor: float = None):
    """Nearest overhead supply and how old it is.

    "Discard those that have overhead resistance nearby" (p.115). Age matters:
    resistance "close to two years old... made it far less potent" (p.119).

    ref_price   -- measure from here. For a base this is the buy-stop, so the
                   top of the base itself is not mistaken for resistance.
    floor       -- ignore highs at or below this level. For a Stage 2 stock
                   it is the breakout level: those highs were already cleared.
                   Highs ABOVE it count however recent -- a fresh peak a few
                   weeks old is the most potent supply there is (p.119 fn).
    """
    span = min(len(weekly), cfg["resistance_scan_years"] * 52)
    seg = weekly.iloc[-span:]
    price = float(seg["Close"].iloc[-1]) if ref_price is None else float(ref_price)
    lo = price * 1.005
    if floor is not None:
        lo = max(lo, float(floor) * 1.005)

    n = len(seg)
    stale_w = cfg["resistance_stale_weeks"]
    near_pct = cfg["resistance_near_pct"]
    dist_of = lambda h: (h - price) / price * 100

    # HOW MUCH supply is overhead. The book separates "some (but not
    # overwhelming) resistance" and a rally that merely "failed" at a level
    # from HEAVY supply: a trading area the stock spent real time in, above all
    # one where it broke down or was turned back again and again (p.110, p.145:
    # Pan Am's "heavy supply near 7 where it broke down", tops "above 8 in each
    # of the four years"). Supply comes from holders who bought up there, so it
    # is measured as the number of non-stale weeks the stock traded inside the
    # nearby band above price. A range's worth -- base_min_weeks -- is heavy.
    recent = seg.iloc[max(0, n - 1 - stale_w):n - 1]
    typ = (recent["High"] + recent["Low"] + recent["Close"]) / 3
    in_band = (typ > lo) & (typ <= price * (1 + near_pct / 100))
    weeks_over = int(in_band.sum())
    heavy = weeks_over >= cfg["base_min_weeks"]

    highs = [(i, float(seg["High"].iloc[i]))
             for i in pivot_highs(seg, cfg["pivot_k"])]
    above = [(i, h) for i, h in highs if h > lo]

    # repeated tops: the most swing highs, within the cluster tolerance of one
    # another, among the fresh levels in the nearby band
    fresh = sorted(h for i, h in above
                   if dist_of(h) <= near_pct and n - 1 - i < stale_w)
    tests = 0
    for a in fresh:
        tests = max(tests, sum(1 for b in fresh
                               if abs(b - a) / a <= cfg["resistance_cluster_tol"]))
    heavy = heavy or tests >= cfg["resistance_min_tests"]

    if not above and not weeks_over:
        # "There is no further resistance to slow down the advance" (p.134)
        return {"resistance_level": None, "resistance_pct": None,
                "resistance_age_wks": None, "resistance_weeks_over": 0,
                "resistance_tests": 0,
                "resistance_clear": True, "resistance_note": "clear"}

    # Every level inside the "nearby" band counts, and one that is still fresh
    # stands out even if an ancient level happens to sit a little closer.
    near_lv = [(i, h) for i, h in above if dist_of(h) <= near_pct]
    live = [(i, h) for i, h in near_lv if n - 1 - i < stale_w]

    if live:
        i, lvl = min(live, key=lambda t: t[1])        # first fresh obstacle
    elif weeks_over:
        # supply without a clean swing high: the nearest overhead trading
        hi_in_band = np.where(in_band.to_numpy(), recent["High"].to_numpy(), np.inf)
        k_ = int(np.argmin(hi_in_band))
        lvl = float(hi_in_band[k_])
        i = max(0, n - 1 - stale_w) + k_
    elif near_lv:
        i, lvl = min(near_lv, key=lambda t: t[1])     # only stale ones nearby
    else:
        i, lvl = min(above, key=lambda t: t[1])       # nothing close

    age, dist = n - 1 - i, dist_of(lvl)
    if heavy:
        why = (f"{weeks_over}wk" if weeks_over >= cfg["base_min_weeks"]
               else f"{tests} tests")
        note = f"HEAVY +{dist:.1f}% ({why})"
    elif live or weeks_over:
        note = f"light +{dist:.1f}% {age}w"
    elif near_lv:
        note = "old"
    else:
        note = "clear"
    return {
        "resistance_level": round(lvl, 2),
        "resistance_pct": round(dist, 1),
        "resistance_age_wks": int(age),
        "resistance_weeks_over": weeks_over,
        "resistance_tests": tests,
        "resistance_clear": not heavy,
        "resistance_note": note,
    }


# ----------------------------------------------------------------------------
# PER-STOCK ANALYSIS
# ----------------------------------------------------------------------------

def heavy_volume(vol, i, cfg):
    """The book's breakout-volume test at weekly bar i (p.104).

    (a) "a one-week volume spike that is at least twice the average volume of
        the past month", or
    (b) "a volume build-up over the past three to four weeks that is at least
        twice the average volume of the past several weeks coupled with at
        least some increase on the breakout week".
    """
    n = cfg["vol_base_weeks"]
    out = {"ratio": np.nan, "spike": False, "buildup": False, "heavy": False}
    if i < n or i >= len(vol):
        return out
    base = float(np.mean(vol[i - n:i]))
    if base > 0:
        out["ratio"] = float(vol[i]) / base
        out["spike"] = out["ratio"] >= cfg["breakout_vol_mult"]
    bw, pw = cfg["buildup_weeks"], cfg["buildup_base_weeks"]
    if i >= bw + pw:
        build = float(np.mean(vol[i - bw:i]))
        prior = float(np.mean(vol[i - bw - pw:i - bw]))
        out["buildup"] = bool(prior > 0 and build / prior >= cfg["breakout_vol_mult"]
                              and vol[i] >= build)
    out["heavy"] = bool(out["spike"] or out["buildup"])
    return out


def long_range(weekly10, price, near_pct=20.0):
    """The Mansfield 10-year 'long-range perspective' (p.99): is the stock in
    virgin territory, and how many of the past ten years' highs sit just above?"""
    if weekly10 is None or len(weekly10) < 60:
        return {}
    w = weekly10.iloc[:-1] if len(weekly10) > 1 else weekly10
    hi = float(w["High"].max())
    yearly = w["High"].groupby(w.index.year).max()
    above = [float(h) for h in yearly if h > price]
    near = [h for h in above if h <= price * (1 + near_pct / 100)]
    return {"lr_virgin": bool(price >= hi), "lr_hi10": round(hi, 2),
            "lr_near_years": len(near), "lr_years": int(len(yearly)),
            "lr_near_level": round(min(near), 2) if near else None}


def breadth_gauges(closes, index_close):
    """Breadth gauges from the book's Chapter 8, computed on the screened
    universe (S&P 1500) rather than the NYSE: the A-D line versus the index
    (p.275), the Momentum Index = 200-day average of daily net advances
    (p.283), and net new highs minus new lows (p.287)."""
    out = []
    chg = closes.diff()
    valid = chg.notna().sum(axis=1)
    keep = valid >= 0.5 * closes.shape[1]
    net = ((chg > 0).sum(axis=1) - (chg < 0).sum(axis=1))[keep]
    if len(net) < 260:
        return out
    ad = net.cumsum()
    mi = net.rolling(200).mean()
    mi_now = float(mi.iloc[-1])
    sign = np.sign(mi.dropna())
    run = 0
    for v in sign.iloc[::-1]:
        if v == sign.iloc[-1]:
            run += 1
        else:
            break
    out.append({"name": "Momentum Index (200-day avg of net advances)",
                "status": "pos" if mi_now > 0 else "neg",
                "detail": f"{mi_now:+.0f}, {'above' if mi_now > 0 else 'below'} zero for {run} sessions"})
    idx = index_close.reindex(ad.index).ffill()
    near_hi = float(idx.iloc[-20:].max()) >= float(idx.iloc[-126:].max()) * 0.995
    ad_conf = float(ad.iloc[-20:].max()) >= float(ad.iloc[-126:].max())
    if near_hi and not ad_conf:
        st, det = "neg", "index at a 6-month high, A-D line has not confirmed (negative divergence)"
    elif near_hi:
        st, det = "pos", "index and A-D line both at 6-month highs"
    else:
        st, det = "neutral", "index below its 6-month high; no divergence test"
    out.append({"name": "Advance-decline line vs index", "status": st, "detail": det})
    hi52 = closes.rolling(252, min_periods=252).max()
    lo52 = closes.rolling(252, min_periods=252).min()
    nh = (closes >= hi52).sum(axis=1)
    nl = (closes <= lo52).sum(axis=1)
    nn = (nh - nl).iloc[-20:].mean()
    out.append({"name": "New 52-wk highs minus lows (20-day avg)",
                "status": "pos" if nn > 0 else "neg", "detail": f"{nn:+.0f} per day"})
    return out


def analyse(ticker, weekly, daily, index_weekly, cfg, group_stage=None):
    if weekly is None or len(weekly) < cfg["ma_length"] + 12:
        return None

    close = weekly["Close"]
    price = float(close.iloc[-1])
    if price < cfg["min_price"]:
        return None

    avg_dollar = float((daily["Close"] * daily["Volume"]).tail(50).mean()) \
        if daily is not None and len(daily) else price * float(weekly["Volume"].tail(4).mean()) / 5
    if cfg["min_dollar_volume"] and avg_dollar < cfg["min_dollar_volume"]:
        return None

    ma = infra.moving_average(close, cfg["ma_length"], cfg["ma_type"])
    if np.isnan(ma.iloc[-1]):
        return None
    stages, bo_series = classify(weekly, ma, cfg)
    stage = int(stages.iloc[-1]) if not np.isnan(stages.iloc[-1]) else 0

    prev = ma.shift(cfg["slope_lookback"])
    slope = float(((ma - prev) / prev.abs()).iloc[-1])
    ma_state = ("Rising" if slope > cfg["ma_flat_tol"]
                else "Falling" if slope < -cfg["ma_flat_tol"] else "Flat")

    # weeks in the current stage
    dur = 0
    for v in stages.iloc[::-1]:
        if v == stages.iloc[-1]:
            dur += 1
        else:
            break

    # --- volume, measured against the prior four weeks (p.105, p.150) ---
    n = cfg["vol_base_weeks"]
    base_vol = float(weekly["Volume"].iloc[-(n + 1):-1].mean())
    vol_ratio = float(weekly["Volume"].iloc[-1] / base_vol) if base_vol else np.nan

    # the test applies to the BREAKOUT week (the first week of Stage 2), not
    # to whatever week happens to be the latest one
    vol_arr = weekly["Volume"].to_numpy(dtype=float)
    bo_i = len(weekly) - dur
    cur_v = heavy_volume(vol_arr, len(weekly) - 1, cfg)
    bo_v = heavy_volume(vol_arr, bo_i, cfg) if stage == 2 else cur_v

    # --- pullback volume against the BREAKOUT's peak (p.105) ---
    # the peak is taken from the first weeks of this Stage 2 only; using any
    # high-volume week (an earnings spike, say) would make almost every
    # pullback look quiet by comparison
    start = len(weekly) - dur
    win = weekly["Volume"].iloc[start:min(start + cfg["breakout_peak_weeks"], len(weekly) - 1)]
    peak_vol = float(win.max()) if len(win) else 0.0
    vol_vs_peak = float(weekly["Volume"].iloc[-1] / peak_vol) if peak_vol else np.nan

    # --- relative strength (p.110-113) ---
    rs_series = infra.mansfield_rs(close, index_weekly["Close"], cfg["rs_length"])
    rs = float(rs_series.iloc[-1]) if not np.isnan(rs_series.iloc[-1]) else np.nan
    k = cfg["rs_trend_weeks"]
    rs_improving = bool(len(rs_series.dropna()) > k
                        and rs > float(rs_series.iloc[-1 - k]))

    # RS against the peak of the base (p.113): Acme's breakout had "the RS
    # line lower than it was at point A, even though the price line was
    # higher". Only matters when RS is also below zero.
    rs_pk = np.nan
    if stage == 2 and bo_i > 0:
        pk_win = weekly["High"].iloc[max(0, bo_i - cfg["base_window"]):bo_i]
        if len(pk_win):
            try:
                rs_pk = float(rs_series.loc[pk_win.idxmax()])
            except Exception:
                rs_pk = np.nan
    rs_below_peak = bool(not np.isnan(rs) and not np.isnan(rs_pk) and rs < rs_pk)

    rng = trading_range(weekly, ma, cfg) or {}

    bo_level = float(bo_series.iloc[-1]) if not np.isnan(bo_series.iloc[-1]) else np.nan
    if stage == 2 and bo_level > 0:
        bo_level = cleared_ceiling(weekly, bo_level, price, dur, cfg)
    pct_above_bo = ((price - bo_level) / bo_level * 100) if bo_level > 0 else np.nan

    # --- overhead resistance, measured from the right place ---
    if stage == 2:
        # highs at or below the breakout level were cleared; anything above
        # it counts, including this advance's own recent peak
        # The floor only applies once price is ABOVE the breakout level (highs
        # below it were cleared). A stock that has slipped back under its
        # breakout level has the old range's top weeks overhead again.
        res = overhead_resistance(
            weekly, cfg, floor=bo_level if (bo_level > 0 and price > bo_level) else None)
    elif rng.get("is_range"):
        res = overhead_resistance(weekly, cfg, ref_price=rng["buy_stop"])
    else:
        res = overhead_resistance(weekly, cfg)

    # --- continuation breakout (p.71): the stock "drops back close to its MA
    # --- and consolidates. It then breaks out anew above the top of its
    # --- resistance zone." (Swift Energy's second breakout, p.62)
    cl = cfg["continuation_lookback"]
    if len(weekly) > cl:
        win = weekly.iloc[-(cl + 1):-1]
        cons_top = float(win["High"].max())
        low_pos = int(np.argmin(win["Low"].to_numpy()))
        ma_at_low = float(ma.iloc[-(cl + 1) + low_pos])
        cons_near_ma = bool(float(win["Low"].iloc[low_pos])
                            <= ma_at_low * (1 + cfg["consol_near_ma_pct"] / 100))
    else:
        cons_top, cons_near_ma = np.inf, False
    cons_low = float(win["Low"].min()) if len(weekly) > cl else float(weekly["Low"].min())
    new_high = price > cons_top
    pct_above_cons = (price - cons_top) / cons_top * 100 if np.isfinite(cons_top) else np.nan

    # --- was there a base before this Stage 2? (p.33, p.139) ---
    # Stage 2 follows a Stage 1 base; "don't guess a bottom... buy on breakouts
    # above resistance". Count the unbroken run of basing weeks (Stage 1, or
    # Stage 3 for a stock that topped and re-based) just before Stage 2 began.
    base_start, base_weeks_before, prior_stage = basing_run(
        stages.to_numpy(), dur, cfg["base_min_weeks"])
    # A stock that was already advancing, dipped under its MA briefly and then
    # broke out to new highs is re-starting an advance, not emerging from a
    # decline (p.71, p.200). The "needs a base" rule is about the latter.
    # "Already advancing" = a real Stage 2 run (8+ weeks) within the last two
    # years, whatever the labels did in between.
    stv_all = stages.to_numpy()
    lo_i = max(0, len(stv_all) - dur - cfg["advance_lookback_weeks"])
    run = best = 0
    for v in stv_all[lo_i:len(stv_all) - dur]:
        run = run + 1 if v == 2 else 0
        best = max(best, run)
    after_advance = best >= cfg["base_min_weeks"]
    # ...and the price range is the better witness to a base than the noisy
    # stage labels, so either one can establish it.
    r_weeks, r_lo, r_hi = range_before(weekly, len(weekly) - dur, cfg["base_max_width"])
    stage_base_weeks = base_weeks_before
    use_range = r_weeks >= cfg["base_min_weeks"] and r_weeks >= stage_base_weeks
    base_weeks_before = max(stage_base_weeks, r_weeks)

    # --- initial protective stop (p.183, p.186, p.196) ---
    # "Pay less attention to the MA and more to the prior correction low": the
    # initial stop goes under the floor of the base the stock broke out of (the
    # consolidation low, for a continuation buy). The "keep it below the MA"
    # rule (p.184, p.190) governs RAISING a stop on a stock already held, so it
    # is not used for a new purchase.
    # The floor is the low of the BASE itself, not of the decline before it.
    start = len(weekly) - dur
    if use_range:
        base_floor = float(r_lo)
    else:
        pre = weekly["Low"].iloc[base_start:start] if stage_base_weeks else \
            weekly["Low"].iloc[max(0, start - cfg["base_window"]):start]
        base_floor = float(pre.min()) if len(pre) else float(weekly["Low"].min())
    # --- triple-confirmation pattern (p.150-152): flag only ---
    triple = {"triple_vol": None, "triple_rs": None, "triple_adv": None, "triple_score": None}
    if stage == 2 and bo_i >= cfg["vol_base_weeks"]:
        nb = cfg["vol_base_weeks"]
        pre = float(np.mean(vol_arr[bo_i - nb:bo_i]))
        follow_ok = True
        if dur > 1 and pre > 0:
            follow_ok = float(np.mean(vol_arr[bo_i + 1:])) / pre >= cfg["triple_follow_mult"]
        t_vol = bool(bo_v["spike"] and follow_ok)
        rs_b = float(rs_series.iloc[bo_i - 1]) if bo_i - 1 < len(rs_series) else np.nan
        t_rs = bool(not np.isnan(rs_b) and not np.isnan(rs) and rs_b <= cfg["triple_rs_before_max"]
                    and rs > 0 and rs > rs_b)
        t_adv = bool(base_floor > 0 and bo_level > 0
                     and (bo_level / base_floor - 1) * 100 >= cfg["triple_prior_advance_pct"])
        triple = {"triple_vol": t_vol, "triple_rs": t_rs, "triple_adv": t_adv,
                  "triple_score": int(t_vol) + int(t_rs) + int(t_adv)}

    stop_base = book_stop(base_floor, cfg["stop_tick"])
    stop_cons = book_stop(cons_low, cfg["stop_tick"])
    stop = stop_base
    # trailed stop for a pullback buy: under the latest completed correction's
    # low, or under the MA when that low sits above it ("correction low is 21,
    # while the MA is up to 20" -> 19 7/8, p.194). Stops only move up.
    corr = trailed_correction_low(weekly, start, cfg["stop_correction_pct"])
    stop_trail = None
    if corr is not None:
        stop_trail = max(stop_base, book_stop(min(corr, float(ma.iloc[-1])), cfg["stop_tick"]))

    dollars = cfg["account_size"] / max(cfg["positions"], 1)

    m = {
        "ticker": ticker,
        "price": round(price, 2),
        "stage": stage,
        "stage_weeks": dur,
        "base_weeks_before": base_weeks_before,
        "after_advance": bool(after_advance),
        "ma30": round(float(ma.iloc[-1]), 2),
        "ma_state": ma_state,
        "group_stage": group_stage,
        "vol_ratio_4wk": round(vol_ratio, 2) if not np.isnan(vol_ratio) else None,
        "vol_vs_peak": round(vol_vs_peak, 2) if not np.isnan(vol_vs_peak) else None,
        "bo_vol_ratio": round(bo_v["ratio"], 2) if not np.isnan(bo_v["ratio"]) else None,
        "bo_heavy": bool(bo_v["heavy"]),
        "bo_buildup": bool(bo_v["buildup"] and not bo_v["spike"]),
        "cur_heavy": bool(cur_v["heavy"]),
        "rs": round(rs, 1) if not np.isnan(rs) else None,
        "rs_improving": rs_improving,
        "rs_at_peak": round(rs_pk, 1) if not np.isnan(rs_pk) else None,
        "rs_below_peak": rs_below_peak,
        "breakout_level": round(bo_level, 2) if bo_level > 0 else None,
        "pct_above_breakout": round(pct_above_bo, 1) if not np.isnan(pct_above_bo) else None,
        "pct_above_ma": round((price / float(ma.iloc[-1]) - 1) * 100, 1),
        "new_high": bool(new_high),
        "consol_top": round(cons_top, 2) if np.isfinite(cons_top) else None,
        "consol_near_ma": cons_near_ma,
        "consol_width_pct": round((cons_top - cons_low) / cons_low * 100, 1) if np.isfinite(cons_top) and cons_low > 0 else None,
        "pct_above_consol": round(pct_above_cons, 1) if not np.isnan(pct_above_cons) else None,
        "stop": round(stop, 2),
        "history_weeks": int(len(weekly)),
        "stop_pct": round((stop - price) / price * 100, 1),
        "trail_stop": round(stop_trail, 2) if stop_trail is not None else None,
        "trail_stop_pct": round((stop_trail - price) / price * 100, 1) if stop_trail is not None else None,
        "wide_stop": bool((stop - price) / price * 100 < -cfg["wide_stop_pct"]),
        "shares": int(dollars / price) if price else 0,
        "avg_dollar_vol_m": round(avg_dollar / 1e6, 1),
    }
    m.update(triple)
    m.update({k2: v for k2, v in rng.items()})
    if rng:
        # "never enter your order to buy until after you've calculated exactly
        # where your protective stop should be placed" (p.183): under the floor
        # of the base, measured from the buy-stop price
        bstop = book_stop(rng["range_bottom"], cfg["stop_tick"])
        brisk = (bstop - rng["buy_stop"]) / rng["buy_stop"] * 100
        m["base_stop"] = round(bstop, 2)
        m["base_risk_pct"] = round(brisk, 1)
        m["base_wide"] = bool(brisk < -cfg["wide_stop_pct"])
    m.update(res)
    # the verdict as it would stand with resistance ignored: stops are chosen
    # from it, and it lets discarded candidates be listed with what they were
    v0 = verdict(m, dict(cfg, resistance_mode="flag"))
    m["verdict_no_res"] = v0
    m["stop_basis"] = "base floor"
    if v0 == "PULLBACK - BUY" and stop_trail is not None:
        pct = (stop_trail - price) / price * 100
        m["stop_basis"] = "trailed stop (correction low or MA, p.194)"
        m.update({"stop": round(stop_trail, 2), "stop_pct": round(pct, 1),
                  "wide_stop": bool(pct < -cfg["wide_stop_pct"])})
    if v0 == "CONTINUATION - BUY":
        # the floor this breakout came out of is the consolidation, not the
        # original base (p.71)
        m["stop_basis"] = "consolidation low"
        pct = (stop_cons - price) / price * 100
        m.update({"stop": round(stop_cons, 2), "stop_pct": round(pct, 1),
                  "wide_stop": bool(pct < -cfg["wide_stop_pct"])})
    m["verdict"] = verdict(m, cfg)
    return m


def verdict(m, cfg):
    """Weinstein's buy/avoid logic.

    "Too high" is judged the way the book judges it: distance from the proper
    ENTRY POINT (the breakout level, or the top of a continuation base), never
    distance from the 30-week MA or the size of the prior gain. Swift Energy
    was not "too high" after 150% (p.62) because it consolidated back to its
    MA and gave a fresh entry point -- but "don't buy it too late in an
    advance, when it is far above the ideal entry point" (p.139).
    """
    stage = m["stage"]

    # "you are never going to buy a stock in this stage because the reward/risk
    # ratio is strongly stacked against you" (p.38)
    if stage == 3:
        return "AVOID - STAGE 3 TOP"
    if stage == 4:
        return "AVOID - STAGE 4"

    # "Stocks trading beneath their 30-week MAs should never be considered for
    # purchase, especially if the MA is declining" (p.14)
    if m["price"] < m["ma30"]:
        return "AVOID - BELOW MA"

    # relative strength (p.110, p.113)
    rs = m["rs"]
    if rs is not None and rs < 0:
        if (not m["rs_improving"] or rs < -cfg["rs_deep_negative"]
                or m.get("rs_below_peak")):
            return "AVOID - WEAK RS"

    # "Discard those that have overhead resistance nearby" (p.115). In the
    # default "flag" mode the stock stays in the list and the resistance
    # column tells you to look; "strict" applies the rule literally.
    if cfg.get("resistance_mode", "flag") == "strict" \
            and not m.get("resistance_clear", True):
        return "WATCH - RESISTANCE OVERHEAD"

    vol = m["vol_ratio_4wk"] or 0
    # the breakout's volume is judged on the breakout week, by either of the
    # book's two tests (p.104); older callers that only pass the latest week's
    # ratio fall back to the simple 2x spike.
    heavy_now = m.get("cur_heavy")
    if heavy_now is None:
        heavy_now = vol >= cfg["breakout_vol_mult"]
    heavy_bo = m.get("bo_heavy")
    if heavy_bo is None:
        heavy_bo = heavy_now
    heavy = heavy_now
    quiet = vol <= cfg["pullback_vol_max"]
    chase = cfg["max_chase_pct"]

    if stage == 1:
        # coiled in a base: this is the buy-stop watchlist entry (p.115)
        if m.get("is_range"):
            return "BUY-STOP WATCH"
        return "WATCH - BASING"

    # ---- Stage 2 ----
    # An initial breakout or its pullback needs a base behind it. (A continuation
    # buy has its own consolidation test below.) The book gives no minimum
    # length -- "months or, in some cases, years" (p.33) -- so the 8 weeks used
    # for a range elsewhere in this script applies.
    bw = m.get("base_weeks_before")
    no_base = (bw is not None and bw < cfg["base_min_weeks"]
               and not m.get("after_advance"))

    # continuation: new high out of a base that came back near the MA, with
    # the MA "clearly trending higher. This is important!" (p.71)
    # ...and it must actually BE a consolidation -- a resistance zone, not a
    # rally that merely brushed the MA (p.71). Same width limit as any range.
    cw = m.get("consol_width_pct")
    is_consol = cw is None or cw <= cfg["base_max_width"] * 100
    fresh_now = m["stage_weeks"] <= cfg["fresh_weeks"]
    continuation = bool(m["new_high"] and (heavy_now or (fresh_now and heavy_bo))
                        and m.get("consol_near_ma")
                        and is_consol and m.get("ma_state") == "Rising")

    def continuation_verdict():
        pc = m.get("pct_above_consol")
        if pc is not None and pc > chase:
            return WAIT_VERDICT
        return "CONTINUATION - BUY"

    if m["stage_weeks"] <= cfg["fresh_weeks"]:
        if m.get("after_advance") and continuation:
            # the stage count reset, but this is the book's continuation buy
            return continuation_verdict()
        if no_base:
            # Stage 2 just restarted but there was no Stage 1 base. If the stock
            # was already advancing, consolidated near its MA, and is breaking
            # out anew, that is the book's continuation buy (p.71) -- the stage
            # count merely reset when price dipped under the MA.
            return continuation_verdict() if continuation else NO_BASE_VERDICT
        if not heavy_bo:
            # "If the volume pattern is negative (not high enough on
            # breakout), sell the stock on the first rally" (p.116)
            return "SUSPECT - LOW VOLUME BREAKOUT"
        pb = m.get("pct_above_breakout")
        if pb is not None and pb > chase:
            # already far above the entry point; ~80% of initial breakouts
            # pull back toward it (p.72), so wait for that
            return WAIT_VERDICT
        return "BREAKOUT - BUY"

    if continuation:
        return continuation_verdict()

    # "That dip brings the stock back close to the breakout point, which is a
    # good second chance to do low-risk buying" (p.34), and volume should
    # contract on the decline (p.115)
    pb = m.get("pct_above_breakout")
    vp = m.get("vol_vs_peak")
    if no_base and pb is not None and -cfg["pullback_below"] <= pb <= cfg["pullback_band"]:
        return NO_BASE_VERDICT
    # p.105: volume "contracted by over 75 percent from peak levels"
    pb_quiet = (vp <= cfg["pullback_vol_peak_max"]) if vp is not None else quiet
    if pb is not None and pb_quiet:
        if -cfg["pullback_below"] <= pb <= cfg["pullback_band"]:
            return "PULLBACK - BUY"

    return "STAGE 2 - HOLD"


def completed_weekly(daily, include_partial=False, today=None):
    """Weekly bars from daily data, dropping a week still in progress.

    Weinstein works from weekly charts and closes. A bar that is only Mon-Wed
    has a fraction of a week's volume, which makes every stock look like it is
    trading on dried-up volume and makes a 2x breakout impossible to see.
    """
    wk = infra._to_weekly(daily)
    if include_partial or wk.empty:
        return wk
    today = pd.Timestamp.today().normalize() if today is None else pd.Timestamp(today)
    label = wk.index[-1]                     # the Friday that ends this bar
    last = daily.index[-1].normalize()
    holiday_week = last.weekday() == 3 and today > last   # Thursday, Friday closed
    if last < label and today <= label and not holiday_week:
        wk = wk.iloc[:-1]
    return wk


def print_detail(df):
    """Per-ticker breakdown, for checking a name against the chart."""
    for _, r in df.iterrows():
        print("-" * 60)
        print(f"{r['ticker']}   {r['verdict']}")
        print(f"  price {r['price']}   30wk MA {r['ma30']} ({r['ma_state']})   "
              f"{r['pct_above_ma']:+.1f}% vs MA")
        print(f"  stage {r['stage']} for {r['stage_weeks']} weeks "
              f"(after {r['base_weeks_before']} weeks of basing"
              f"{', following an earlier advance' if r['after_advance'] else ''})   "
              f"group stage {r.get('group_stage')}")
        if r["stage"] == 2:
            print(f"  breakout level {r['breakout_level']}   "
                  f"now {r['pct_above_breakout']}% above it")
            print(f"  last consolidation top {r['consol_top']}   "
                  f"came back near MA: {r['consol_near_ma']}   "
                  f"now {r['pct_above_consol']}% above it")
        if r["stage"] == 1 and r.get("is_range") == True:  # noqa: E712 (NaN-safe)
            print(f"  base {r['range_bottom']}-{r['range_top']} "
                  f"({r['range_width_pct']}% wide, {r['range_weeks']} wks)   "
                  f"buy-stop {r['buy_stop']} ({r['pct_to_trigger']}% away)")
        vp = "n/a (first week)" if pd.isna(r["vol_vs_peak"]) else f"{r['vol_vs_peak']}x"
        print(f"  volume {r['vol_ratio_4wk']}x prior 4 wks, {vp} the breakout's peak week   RS {r['rs']} "
              f"(improving: {r['rs_improving']})")
        if r["resistance_level"] is None or pd.isna(r["resistance_level"]):
            yrs = r["history_weeks"] / 52
            print(f"  overhead resistance: none found in {yrs:.1f} years of data "
                  f"(older highs than that are not checked)")
        else:
            print(f"  overhead resistance: {r['resistance_level']} "
                  f"(+{r['resistance_pct']}%, {r['resistance_age_wks']} wks old) "
                  f"-> {r['resistance_note']}")
        print(f"  stop {r['stop']} ({r['stop_pct']}%, under the {r['stop_basis']})"
              f"{'  ** WIDE' if r['wide_stop'] else ''}   shares {r['shares']}")
    print("-" * 60 + "\n")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Weinstein-only screener")
    p.add_argument("--universe", default="sp1500",
                   choices=["sp1500", "sp500", "all"])
    p.add_argument("--tickers")
    p.add_argument("--file")
    p.add_argument("--detail", action="store_true")
    p.add_argument("--all", action="store_true",
                   help="ignore the market/group filter")
    p.add_argument("--account", type=float)
    p.add_argument("--positions", type=int)
    p.add_argument("--out", default="weinstein_results.csv")
    p.add_argument("--watchlist", default="weinstein_watchlist.txt")
    p.add_argument("--no-breadth", action="store_true",
                   help="skip the market-breadth gauges (screens favorable groups only; faster)")
    p.add_argument("--feed", help="dashboard JSON path (default weinstein_feed.json "
                   "on full runs)")
    p.add_argument("--chunk", type=int, default=100)
    p.add_argument("--max-new", type=int, default=150)
    p.add_argument("--rounds", type=int, default=6)
    p.add_argument("--resistance", choices=["flag", "strict"], default="strict",
                   help="strict = discard stocks with nearby overhead resistance "
                        "(default, per the book); flag = keep them and just show it")
    p.add_argument("--include-partial", action="store_true",
                   help="keep the in-progress week (default: use the last "
                        "completed Friday close)")
    p.add_argument("--max-chase", type=float,
                   help=f"%% above the entry point that counts as too late "
                        f"(default {CFG['max_chase_pct']})")
    args = p.parse_args()

    cfg = dict(CFG)
    cfg["resistance_mode"] = args.resistance
    if args.max_chase is not None:
        cfg["max_chase_pct"] = args.max_chase
    if args.account:
        cfg["account_size"] = args.account
    if args.positions:
        cfg["positions"] = args.positions

    import yfinance as yf

    # ---- 1. "Check the major trend of the overall market." (p.115) ----
    idx = yf.download("^GSPC", period="5y", interval="1d",
                      auto_adjust=True, progress=False)
    if isinstance(idx.columns, pd.MultiIndex):
        idx.columns = idx.columns.droplevel(1)
    index_weekly = completed_weekly(idx, args.include_partial)
    idx_ma = infra.moving_average(index_weekly["Close"], cfg["ma_length"], cfg["ma_type"])
    mkt_stage = int(classify(index_weekly, idx_ma, cfg)[0].iloc[-1])
    print(f"\nMARKET (S&P 500): Stage {mkt_stage}", file=sys.stderr)
    if mkt_stage == 4:
        print("  Stage 4 -- Weinstein would be defensive here, not buying.",
              file=sys.stderr)

    # Chapter 8's "Weight of the Evidence": stage analysis of the Dow is the one
    # indicator "you have no choice" about (p.270); the world average (p.294)
    # and General Motors (p.297) get the same 30-week stage test.
    def _stage_of(tk):
        try:
            d = yf.download(tk, period="5y", interval="1d", auto_adjust=True, progress=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.droplevel(1)
            wk = completed_weekly(d, args.include_partial)
            m_ = infra.moving_average(wk["Close"], cfg["ma_length"], cfg["ma_type"])
            return int(classify(wk, m_, cfg)[0].iloc[-1])
        except Exception:
            return None

    STAGE_NAME = {1: "Stage 1 (basing)", 2: "Stage 2 (advancing)",
                  3: "Stage 3 (topping)", 4: "Stage 4 (declining)"}

    def _stage_gauge(name, st):
        if st is None:
            return None
        status = "pos" if st == 2 else "neg" if st == 4 else "neutral"
        return {"name": name, "status": status, "detail": STAGE_NAME.get(st, str(st))}

    gauges = [_stage_gauge("S&P 500 vs 30-week MA", mkt_stage)]
    dow_stage = _stage_of("^DJI")
    gauges.append(_stage_gauge("Dow Jones Industrials vs 30-week MA", dow_stage))
    gauges.append(_stage_gauge("World stock average (ACWI) vs 30-week MA", _stage_of("ACWI")))
    gauges.append(_stage_gauge("General Motors vs 30-week MA", _stage_of("GM")))
    gauges = [g for g in gauges if g]
    if dow_stage == 4:
        print("  Dow is Stage 4 -- suspend buying (p.270).", file=sys.stderr)

    # ---- 2. "Uncover the few groups that look best technically." ----
    groups = {}
    groups_rs = {}
    gdata = yf.download(list(infra.SECTOR_ETFS.values()), period="4y",
                        interval="1wk", group_by="ticker",
                        auto_adjust=True, progress=False)
    for name, etf in infra.SECTOR_ETFS.items():
        try:
            f = infra._extract_ticker_frame(gdata, etf).dropna()
            gma = infra.moving_average(f["Close"], cfg["ma_length"], cfg["ma_type"])
            groups[name] = int(classify(f, gma, cfg)[0].iloc[-1])
            try:
                _g = float(infra.mansfield_rs(
                    f["Close"], index_weekly["Close"], cfg["rs_length"]).iloc[-1])
                groups_rs[name] = None if math.isnan(_g) else round(_g, 1)
            except Exception:
                groups_rs[name] = None
        except Exception:
            groups[name] = None
    good = {g for g, s in groups.items() if s in (1, 2)}
    print("GROUPS: " + ", ".join(f"{g}={s}" for g, s in sorted(groups.items())),
          file=sys.stderr)
    print(f"Favorable groups (Stage 1 or 2): {', '.join(sorted(good))}\n",
          file=sys.stderr)

    # ---- 3. universe, restricted to favorable groups ----
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
        smap = {}
    elif args.file:
        tickers = [l.strip().upper() for l in open(args.file) if l.strip()]
        smap = {}
    elif args.universe == "sp1500":
        tickers, smap = infra.load_sp1500()
    elif args.universe == "all":
        tickers, smap = infra.load_all_us_listed(), {}
    else:
        ns = argparse.Namespace(tickers=None, file=None, universe="sp500",
                                sectors=None)
        tickers, smap = infra.load_universe(ns)

    universe_all = list(tickers)
    if smap and not args.all:
        before = len(tickers)
        tickers = [t for t in tickers if smap.get(t) in good]
        print(f"Group filter: {before} -> {len(tickers)} tickers\n", file=sys.stderr)

    if not tickers:
        print("Nothing to screen.", file=sys.stderr)
        return

    # ---- 4. fetch + analyse ----
    rows = []
    want = list(tickers)
    if smap and not args.no_breadth and not (args.tickers or args.file):
        want = universe_all            # breadth needs the whole universe
    have = [t for t in want if infra.is_cached(t)]
    missing = [t for t in want if t not in set(have)]
    print(f"Cache: {len(have)}/{len(want)}", file=sys.stderr)

    queue = list(missing)
    for rnd in range(max(1, args.rounds)):
        if not queue:
            break
        if rnd:
            print(f"  round {rnd+1}: pausing 180s", file=sys.stderr)
            import time as _t
            _t.sleep(180)
        got = 0
        while queue and got < args.max_new:
            batch = queue[:args.chunk]
            pr = infra.fetch_prices(batch, quiet=True)
            queue = queue[len(batch):]
            got += len(batch)
            print(f"  fetched {len(pr)}/{len(batch)}   remaining {len(queue)}",
                  file=sys.stderr)
            if len(pr) / max(len(batch), 1) < 0.7:
                print("  throttled -- ending round", file=sys.stderr)
                break

    for t in tickers:
        daily = infra.load_cached(t)
        if daily is None or daily.empty:
            continue
        try:
            r = analyse(t, completed_weekly(daily, args.include_partial),
                        daily, index_weekly, cfg,
                        group_stage=groups.get(smap.get(t)))
            if r:
                r["group"] = smap.get(t, "")
                rows.append(r)
        except Exception as exc:
            print(f"  skip {t}: {exc}", file=sys.stderr)

    if not rows:
        print("No stocks analysed -- is the cache populated?", file=sys.stderr)
        return

    if smap and not args.no_breadth and not (args.tickers or args.file):
        try:
            cl = {}
            for t in universe_all:
                dd = infra.load_cached(t)
                if dd is not None and not dd.empty:
                    cl[t] = dd["Close"].tail(520)
            closes = pd.DataFrame(cl)
            gauges += breadth_gauges(closes, idx["Close"])
            print(f"Breadth gauges computed on {closes.shape[1]} stocks", file=sys.stderr)
        except Exception as exc:
            print(f"  breadth skipped: {exc}", file=sys.stderr)

    df = pd.DataFrame(rows)
    out_path = args.out
    if (args.tickers or args.file) and args.out == "weinstein_results.csv":
        out_path = "weinstein_check.csv"   # spot checks never overwrite the full run
    df.to_csv(out_path, index=False)

    if args.detail or args.tickers:
        print_detail(df)

    # "Don't buy when the overall market trend is bearish." (p.139)
    blocked = (mkt_stage == 4 or dow_stage == 4) and not args.all
    if blocked:
        print("=" * 84)
        print("MARKET (S&P 500 or Dow) IS IN STAGE 4.  Weinstein: don't buy when the overall market "
              "trend is bearish (p.139, p.270).")
        print("All buys below are SUPPRESSED; run with --all to see them anyway.")
        print("=" * 84 + "\n")

    allbuys = df[df["verdict"].isin(BUY_VERDICTS)].sort_values(
        ["stage_weeks", "rs"], ascending=[True, False])
    # p.184: "try to limit your purchases to those cases where the initial stop
    # isn't greater than 15 percent below your purchase price"
    buys = allbuys[~allbuys["wide_stop"]]
    # closest to the 15% limit first: those are the candidates for his
    # "occasional exceptions"
    skips = allbuys[allbuys["wide_stop"]].sort_values("stop_pct", ascending=False)
    watch_all = df[df["verdict"] == "BUY-STOP WATCH"].sort_values("pct_to_trigger")
    watch = watch_all[~watch_all["base_wide"].fillna(False).astype(bool)]
    watch_skip = watch_all[watch_all["base_wide"].fillna(False).astype(bool)].sort_values(
        "base_risk_pct", ascending=False)
    waits = df[df["verdict"] == WAIT_VERDICT].sort_values("stage_weeks")
    # Near misses: a Stage 2 stock in the pullback zone whose trailed stop is
    # inside the 15% limit, rejected ONLY because volume has not contracted by
    # the 75% in the book's example (p.105). The book states the rule as
    # "contracts on the decline" (p.115) and gives 75% as what it saw, so these
    # are listed rather than hidden.
    pbz = df["pct_above_breakout"].between(-cfg["pullback_below"], cfg["pullback_band"])
    nm_mask = ((df["verdict"] == "STAGE 2 - HOLD") & pbz
               & (df["vol_vs_peak"] > cfg["pullback_vol_peak_max"])
               & (df["vol_vs_peak"] < 1.0)
               & df["trail_stop_pct"].notna()
               & (df["trail_stop_pct"] >= -cfg["wide_stop_pct"]))
    near = df[nm_mask].sort_values("vol_vs_peak")
    # Discarded for nearby overhead resistance (p.115, p.139): everything that
    # would otherwise have been a buy, a buy-stop candidate or a near miss
    RES = "WATCH - RESISTANCE OVERHEAD"
    v0 = df["verdict_no_res"]
    nm0 = ((v0 == "STAGE 2 - HOLD") & pbz & (df["vol_vs_peak"] > cfg["pullback_vol_peak_max"])
           & (df["vol_vs_peak"] < 1.0) & df["trail_stop_pct"].notna()
           & (df["trail_stop_pct"] >= -cfg["wide_stop_pct"]))
    disc_mask = (df["verdict"] == RES) & (v0.isin(BUY_VERDICTS | {WAIT_VERDICT, "BUY-STOP WATCH"}) | nm0)
    disc = df[disc_mask].copy()
    disc["would_be"] = np.where(nm0[disc_mask], "NEAR MISS", v0[disc_mask])
    disc = disc.sort_values(["would_be", "resistance_pct"])
    suspects = df[df["verdict"] == "SUSPECT - LOW VOLUME BREAKOUT"]
    all_names = (list(allbuys["ticker"]) + list(watch_all["ticker"]) + list(waits["ticker"])
                 + list(near["ticker"]) + list(disc["ticker"]))
    # ---- dashboard feed: same tables as printed below, as JSON ----
    import json as _json, datetime as _dt

    def _clean(v):
        if v is None:
            return None
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating, float)):
            return None if pd.isna(v) else round(float(v), 4)
        if isinstance(v, (np.bool_,)):
            return bool(v)
        return v

    def _entry(r, kind):
        """entry zone per the book: buy the breakout (p.150), don't chase (p.139)."""
        chase = 1 + cfg["max_chase_pct"] / 100
        if kind == "pullback":
            bo = r["breakout_level"]
            return (bo * (1 - cfg["pullback_below"] / 100), bo * (1 + cfg["pullback_band"] / 100))
        if kind == "base":
            return (r["buy_stop"], r["buy_stop"] * chase)
        fresh = r["stage_weeks"] <= cfg["fresh_weeks"]
        ref = r["breakout_level"] if fresh else r["consol_top"]
        return (ref, ref * chase)

    # the 10-year "long-range perspective" (p.99) for the names that matter
    _cand = pd.concat([buys, skips, watch, watch_skip, waits, near])["ticker"].unique().tolist()
    lr = {}
    if _cand and (args.feed or not (args.tickers or args.file)):
        try:
            g10 = yf.download(_cand, period="10y", interval="1wk", group_by="ticker",
                              auto_adjust=True, progress=False)
            _px = dict(zip(df["ticker"], df["price"]))
            for tk_ in _cand:
                try:
                    f10 = infra._extract_ticker_frame(g10, tk_).dropna()
                    lr[tk_] = long_range(f10, _px[tk_])
                except Exception:
                    pass
        except Exception as exc:
            print(f"  long-range check skipped: {exc}", file=sys.stderr)

    def _pack(frame_, table, stop_col="stop", pct_col="stop_pct"):
        out = []
        for _, r in frame_.iterrows():
            kind = ("pullback" if r["verdict"] == "PULLBACK - BUY" or table in ("near",)
                    else "base" if table in ("watch", "watch_skip")
                    else "breakout")
            d = {k: _clean(r.get(k)) for k in (
                "ticker", "group", "price", "stage_weeks", "vol_ratio_4wk", "vol_vs_peak",
                "rs", "resistance_note", "resistance_level", "resistance_pct",
                "resistance_age_wks", "resistance_weeks_over", "pct_to_trigger",
                "range_weeks", "range_width_pct", "pct_above_breakout", "breakout_level",
                "shares", "avg_dollar_vol_m", "stop_basis", "bo_vol_ratio", "bo_buildup",
                "triple_vol", "triple_rs", "triple_adv", "triple_score", "rs_at_peak",
                "base_weeks_before", "range_bottom", "group_stage")}
            d.update(lr.get(r["ticker"], {}))
            d["verdict"] = r["verdict"]
            d["kind"] = kind
            if table in ("watch", "watch_skip"):
                d["stop"], d["risk_pct"] = _clean(r["base_stop"]), _clean(r["base_risk_pct"])
            elif table == "near":
                d["stop"], d["risk_pct"] = _clean(r["trail_stop"]), _clean(r["trail_stop_pct"])
            elif table == "disc":
                use_t = r["would_be"] == "NEAR MISS"
                d["stop"] = _clean(r["trail_stop"] if use_t else r["stop"])
                d["risk_pct"] = _clean(r["trail_stop_pct"] if use_t else r["stop_pct"])
                d["would_be"] = r["would_be"]
            else:
                d["stop"], d["risk_pct"] = _clean(r["stop"]), _clean(r["stop_pct"])
            try:
                lo, hi = _entry(r, kind)
                d["entry_low"], d["entry_high"] = _clean(lo), _clean(hi)
            except Exception:
                d["entry_low"] = d["entry_high"] = None
            out.append(d)
        return out

    _feed = {
        "generated": _dt.datetime.now().isoformat(timespec="minutes"),
        "last_bar": str(index_weekly.index[-1].date()),
        "market_stage": mkt_stage, "market_blocked": bool(blocked),
        "groups": groups, "groups_rs": groups_rs, "favorable_groups": sorted(good),
        "market": {"gauges": gauges,
                   "pos": sum(g["status"] == "pos" for g in gauges),
                   "neg": sum(g["status"] == "neg" for g in gauges),
                   "caution": sum(g["status"] == "neg" for g in gauges)
                              > sum(g["status"] == "pos" for g in gauges),
                   "dow_stage": dow_stage},
        "screened": int(len(df)),
        "rules": {"wide_stop_pct": cfg["wide_stop_pct"], "max_chase_pct": cfg["max_chase_pct"],
                  "breakout_vol_mult": cfg["breakout_vol_mult"],
                  "pullback_vol_peak_max": cfg["pullback_vol_peak_max"],
                  "positions": cfg["positions"], "account_size": cfg["account_size"],
                  "resistance_near_pct": cfg["resistance_near_pct"]},
        "active": _pack(buys, "active"), "skip_stop": _pack(skips, "skip"),
        "watch": _pack(watch, "watch"), "watch_skip": _pack(watch_skip, "watch_skip"),
        "waits": _pack(waits, "wait"), "near": _pack(near, "near"),
        "disc": _pack(disc, "disc"), "suspects": _pack(suspects, "suspect"),
        "no_base": int((df["verdict"] == NO_BASE_VERDICT).sum()),
    }
    if args.feed or not (args.tickers or args.file):
        with open(args.feed or "weinstein_feed.json", "w") as _f:
            _json.dump(_feed, _f, indent=1)

    n_suppressed = 0
    if blocked:
        n_suppressed = (len(buys) + len(skips) + len(watch) + len(watch_skip)
                        + len(waits) + len(near) + len(disc))
        e = df.iloc[0:0]
        buys, skips, watch, watch_skip, waits = e, e, watch_all.iloc[0:0], watch_all.iloc[0:0], e
        near = near.iloc[0:0]
        disc = disc.iloc[0:0]

    def buy_rows(frame_):
        print(f"{'TICKER':<7}{'GROUP':<13}{'PRICE':>9}{'S2 WKS':>7}{'VOL':>6}{'VS PK':>7}{'RS':>7}"
              f"{'STOP':>9}{'RISK%':>8}  {'VERDICT':<20}RESISTANCE")
        for _, r in frame_.iterrows():
            print(f"{r['ticker']:<7}{str(r['group'])[:12]:<13}{r['price']:>9.2f}"
                  f"{int(r['stage_weeks']):>7}"
                  f"{(r['vol_ratio_4wk'] or 0):>6.1f}"
                  f"{('   -' if pd.isna(r['vol_vs_peak']) else format(r['vol_vs_peak'], '7.2f')):>7}"
                  f"{(r['rs'] or 0):>7.1f}"
                  f"{r['stop']:>9.2f}{r['stop_pct']:>8.1f}  "
                  f"{r['verdict']:<20}{r['resistance_note']}")

    print("=" * 84)
    print(f"ACTIVE BUYS  ({len(buys)})    resistance mode: {cfg['resistance_mode']}"
          f"   (stop within {cfg['wide_stop_pct']:.0f}%)")
    print("=" * 84)
    if buys.empty:
        print("  none this week")
    else:
        buy_rows(buys)

    print()
    print("=" * 84)
    print(f"BOOK SAYS SKIP - STOP TOO WIDE  ({len(skips)})   -- good setup, but the stop is "
          f"more than {cfg['wide_stop_pct']:.0f}% away (p.184)")
    print("=" * 84)
    if skips.empty:
        print("  none this week")
    else:
        buy_rows(skips)
        print("  He allows 'occasional exceptions because a chart pattern is so outstanding'.")

    def watch_rows(frame_, limit=40):
        print(f"{'TICKER':<7}{'GROUP':<13}{'PRICE':>9}{'BUY-STOP':>10}"
              f"{'TO GO%':>8}{'STOP':>9}{'RISK%':>7}{'WKS':>5}{'WIDTH%':>8}{'RS':>7}  RESISTANCE")
        for _, r in frame_.head(limit).iterrows():
            print(f"{r['ticker']:<7}{str(r['group'])[:12]:<13}{r['price']:>9.2f}"
                  f"{r['buy_stop']:>10.2f}{r['pct_to_trigger']:>8.1f}"
                  f"{r['base_stop']:>9.2f}{r['base_risk_pct']:>7.1f}"
                  f"{int(r['range_weeks']):>5}{r['range_width_pct']:>8.1f}"
                  f"{(r['rs'] or 0):>7.1f}  {r['resistance_note']}")
        if len(frame_) > limit:
            print(f"  ...and {len(frame_)-limit} more in {out_path}")

    print()
    print("=" * 96)
    print(f"BUY-STOP WATCHLIST  ({len(watch)})   -- coiled in a trading range, stop under the "
          f"base within {cfg['wide_stop_pct']:.0f}%")
    print("=" * 96)
    if watch.empty:
        print("  none this week")
    else:
        watch_rows(watch)

    print()
    print("=" * 96)
    print(f"BOOK SAYS SKIP - BASE TOO WIDE  ({len(watch_skip)})   -- a stop under the base floor "
          f"would be more than {cfg['wide_stop_pct']:.0f}% away (p.184)")
    print("=" * 96)
    if watch_skip.empty:
        print("  none this week")
    else:
        watch_rows(watch_skip)

    print()
    print("=" * 84)
    print(f"WAIT FOR PULLBACK  ({len(waits)})   -- good breakout, but more than "
          f"{cfg['max_chase_pct']:.0f}% above the entry point (p.139)")
    print("=" * 84)
    if waits.empty:
        print("  none this week")
    else:
        print(f"{'TICKER':<7}{'GROUP':<13}{'PRICE':>9}{'ENTRY':>9}{'ABOVE%':>8}"
              f"{'S2 WKS':>7}{'RS':>7}  RESISTANCE")
        for _, r in waits.iterrows():
            fresh = r["stage_weeks"] <= cfg["fresh_weeks"]
            entry = r["breakout_level"] if fresh else r["consol_top"]
            above = r["pct_above_breakout"] if fresh else r["pct_above_consol"]
            print(f"{r['ticker']:<7}{str(r['group'])[:12]:<13}{r['price']:>9.2f}"
                  f"{(entry or 0):>9.2f}{(above or 0):>8.1f}"
                  f"{int(r['stage_weeks']):>7}{(r['rs'] or 0):>7.1f}  "
                  f"{r['resistance_note']}")

    if blocked:
        print(f"\n{n_suppressed} candidates suppressed because the market is in Stage 4 "
              f"(still listed in {out_path} and the TradingView file).")
    print()
    print("=" * 96)
    print(f"NEAR MISSES  ({len(near)})   -- in the pullback zone, trailed stop within "
          f"{cfg['wide_stop_pct']:.0f}%, but volume is not down 75% from the breakout peak (p.105)")
    print("=" * 96)
    if near.empty:
        print("  none this week")
    else:
        print(f"{'TICKER':<7}{'GROUP':<13}{'PRICE':>9}{'S2 WKS':>7}{'VS PK':>7}{'ABOVE BO%':>10}"
              f"{'STOP':>9}{'RISK%':>7}{'RS':>7}  RESISTANCE")
        for _, r in near.iterrows():
            print(f"{r['ticker']:<7}{str(r['group'])[:12]:<13}{r['price']:>9.2f}"
                  f"{int(r['stage_weeks']):>7}{r['vol_vs_peak']:>7.2f}{r['pct_above_breakout']:>10.1f}"
                  f"{r['trail_stop']:>9.2f}{r['trail_stop_pct']:>7.1f}{(r['rs'] or 0):>7.1f}"
                  f"  {r['resistance_note']}")

    print()
    print("=" * 96)
    print(f"DISCARDED - OVERHEAD RESISTANCE  ({len(disc)})   -- would otherwise qualify, but supply sits within "
          f"{cfg['resistance_near_pct']:.0f}% overhead (p.115, p.139)")
    print("=" * 96)
    if disc.empty:
        print("  none this week")
    else:
        print(f"{'TICKER':<7}{'GROUP':<13}{'PRICE':>9}  {'WOULD BE':<20}{'RES LEVEL':>10}{'DIST%':>7}{'AGE':>6}"
              f"{'WKS OVER':>9}{'RISK%':>7}{'VS PK':>7}")
        for _, r in disc.iterrows():
            rk = r["trail_stop_pct"] if r["would_be"] == "NEAR MISS" else r["stop_pct"]
            vp = "   -" if pd.isna(r["vol_vs_peak"]) else format(r["vol_vs_peak"], "7.2f")
            print(f"{r['ticker']:<7}{str(r['group'])[:12]:<13}{r['price']:>9.2f}  {r['would_be']:<20}"
                  f"{r['resistance_level']:>10.2f}{r['resistance_pct']:>7.1f}{int(r['resistance_age_wks']):>5}w"
                  f"{int(r['resistance_weeks_over']):>9}{rk:>7.1f}{vp:>7}")

    print()
    print("=" * 96)
    print(f"SUSPECT BREAKOUTS  ({len(suspects)})   -- new Stage 2, volume under 2x the prior 4 weeks "
          f"(p.116: if you own it, sell on the first rally)")
    print("=" * 96)
    if suspects.empty:
        print("  none this week")
    else:
        print(f"{'TICKER':<7}{'GROUP':<13}{'PRICE':>9}{'S2 WKS':>7}{'VOL':>6}{'RS':>7}")
        for _, r in suspects.iterrows():
            print(f"{r['ticker']:<7}{str(r['group'])[:12]:<13}{r['price']:>9.2f}"
                  f"{int(r['stage_weeks']):>7}{(r['vol_ratio_4wk'] or 0):>6.1f}{(r['rs'] or 0):>7.1f}")

    nb = int((df["verdict"] == NO_BASE_VERDICT).sum())
    if nb:
        print(f"\n{nb} Stage 2 stocks rejected for having no base behind them "
              f"(V-shaped rallies; see {out_path}, verdict '{NO_BASE_VERDICT}').")
    names = all_names
    if names:
        exm = infra.build_exchange_map()
        files = infra.write_tv_watchlist(names, args.watchlist, exm)
        print(f"\nTradingView watchlist: {', '.join(files)}")
    print(f"Full metrics: {out_path}")
    print("\nPosition sizing: equal dollar amounts, "
          f"{cfg['positions']} positions of "
          f"${cfg['account_size']/cfg['positions']:,.0f} (p.138).")


if __name__ == "__main__":
    main()
