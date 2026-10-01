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
import csv
import io
import math
import os
import sys
import time

import numpy as np
import pandas as pd

import stage_vcp_screener as infra
import universe_fetch as uf


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
    "vol_verify_mult": 20.0,     # breakout volume this many x normal = suspect data (split / relisting artifact); flagged, not dropped. Not from the book.
    "breakout_peak_weeks": 4,   # weeks from Stage 2 entry that set the "peak"

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
    # anew above the top of its resistance zone" (p.61). The book gives no
    # number for "close to"; 8% is our reading of it.
    "consol_near_ma_pct": 8.0,

    # "No matter how bullish a stock is, don't buy it too late in an advance,
    # when it is far above the ideal entry point" (p.129). "If you miss it at
    # 12 1/8, buying at 12 7/8 is no big deal, but paying 25 or 26 sure is!"
    # (p.36). The book gives no exact cutoff; 10% above the entry point is our
    # reading of "far above". About 80% of initial breakouts pull back (p.62),
    # so a stock past this line goes to WAIT, not AVOID.
    "max_chase_pct": 10.0,

    # "strict" -> nearby resistance discards the stock (p.115, p.129 literally);
    #             discarded candidates are still listed in their own table.
    # "flag"   -> nearby resistance is only shown in the RESISTANCE column.
    "resistance_mode": "strict",

    # The initial stop goes "right below the significant floor of support"
    # (p.183) -- the base floor -- one eighth under it, and under a round
    # number or half if it would land just above one (p.183).
    "stop_tick": 0.125,
    # A pullback buy is the second half of a position opened at the breakout
    # (p.34, p.115), so it carries that position's TRAILED stop: raised after
    # the first correction of "at least 8 to 10 percent" (p.184).
    "stop_correction_pct": 0.08,

    # "for a small portfolio ($10,000 to $25,000), I'd diversify into no more
    # than five or six stocks... 10 to 20 stocks are the most that I'd invest
    # in at any one time. Also, use approximately equal dollar amounts" (p.138).
    "account_size": 300_000,
    "positions": 15,

    # The book sets NO minimum price or volume: his own big winners include
    # Blocker Energy at about $1 and a 3/8 low (p.154-157). Nothing is screened
    # out for being cheap or quiet. Instead thinly traded stocks are flagged
    # and handled the way he handles them: a wider buy limit (p.66) and a wide
    # sell-stop-limit spread (p.180). The thresholds below are ours.
    "min_dollar_volume": 0,
    "thin_dollar_volume": 1_000_000,       # average daily dollars: "thin" below this
    "very_thin_dollar_volume": 250_000,    # ...and "very thin" below this
    "thin_max_adv_pct": 5.0,               # cap a position at this % of average daily shares

    # "try to limit your purchases to those cases where the initial stop isn't
    # greater than 15 percent below your purchase price" (p.184), with
    # "occasional exceptions because a chart pattern is so outstanding". So a
    # wider stop is flagged, not dropped.
    "wide_stop_pct": 15.0,
    "range_scan_weeks": 260,
    "ceiling_scan_weeks": 78,    # how far before Stage 2 to look for a range ceiling     # how far back to measure real base length
    "min_price": 0.0,

    # ---- taking profits (Chapter 6, p.164-213) ----
    # The book gives investors NO price targets: the exit is the trailing
    # sell-stop (p.184-186). Targets exist only for traders: the swing rule
    # (p.202-205), a trendline sale of half (p.198-201), and a tight stop.
    "overextended_pct": 40.0,      # "far above its 30-week MA" (p.193): no figure in the book; OURS
    "overextended_sell_frac": "1/4 to 1/2",   # the book's range for the partial sale (p.193)
    "swing_lookback_weeks": 156,   # how far before the breakout to look for the peak A; OURS
    "swing_min_decline_pct": 20.0, # the decline A->B must be "important"; OURS
    "swing_max_gain_pct": 100.0,   # ignore projections more than this far above price; OURS
    "trader_stop_pct": 5.0,        # "4 to 6 percent below the breakout" (p.194-195): midpoint
    "trader_low_weeks": 8,         # "closest prior reaction low": looked for in this many weeks; OURS
    "trader_stop_max_pct": 8.0,    # a reaction low deeper than this is not "closest"; OURS
    "trader_correction_pct": 0.07, # "corrections of less than 7 percent" are ignored (p.195)
    "trail_recover_frac": 0.5,     # "back toward the prior high" = half way (p.184); same reading as the buy side
    "stage3_investor_sell_frac": "half",      # p.36-37
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
    """Low of the latest COMPLETED correction since Stage 2 began (p.184).

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
    # one where it broke down or was turned back again and again (p.100, p.110:
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


def swing_target(weekly, bo_i, price, cfg):
    """The swing rule (p.202-205): "take the peak price before an important
    decline sets in and subtract the next low price from it... add the 10
    points onto the peak price of A once XYZ betters the old peak". A trading
    target only: "sell at least a part of your position near the projection".

    A is the highest high in the look-back before the breakout week bo_i; B is
    the lowest low after A. Returns None when there was no important decline,
    when the target has already been passed, or when it is implausibly far.
    """
    if bo_i is None or bo_i < 20:
        return None
    H = weekly["High"].to_numpy()
    L = weekly["Low"].to_numpy()
    lo = max(0, bo_i - cfg["swing_lookback_weeks"])
    seg = H[lo:bo_i]
    if len(seg) < 20:
        return None
    a_i = lo + int(np.argmax(seg))
    A = float(H[a_i])
    if a_i + 1 > bo_i:
        return None
    B = float(L[a_i + 1:bo_i + 1].min())
    if A <= 0 or B <= 0 or (A - B) / A * 100 < cfg["swing_min_decline_pct"]:
        return None
    T = A + (A - B)
    gain = (T / price - 1) * 100 if price else float("nan")
    if not np.isfinite(gain) or gain < 3.0 or gain > cfg["swing_max_gain_pct"]:
        return None
    return {"swing_peak": round(A, 2), "swing_low": round(B, 2),
            "swing_target": round(T, 2), "swing_gain_pct": round(gain, 1),
            "swing_cleared": bool(float(H[bo_i:].max()) > A)}


def trader_stop(weekly, bo_i, bo_level, cfg):
    """A trader's initial stop (p.194-195): "under the closest prior reaction
    low. If there isn't any, ... 4 to 6 percent below the breakout level",
    beneath a round number (under $20 every half point counts as one)."""
    if not bo_level or bo_level <= 0:
        return None
    lo = float(weekly["Low"].iloc[max(0, bo_i - cfg["trader_low_weeks"]):bo_i].min()) \
        if bo_i and bo_i > 0 else None
    if lo is not None and bo_level * (1 - cfg["trader_stop_max_pct"] / 100) <= lo < bo_level:
        return book_stop(lo, cfg["stop_tick"])
    return book_stop(bo_level * (1 - cfg["trader_stop_pct"] / 100), cfg["stop_tick"])


def trail_stop(weekly, ma, start, cfg, trader=False):
    """Replay the book's trailing sell-stop (p.184-186, p.195-196) from the
    start of Stage 2 to the latest bar and return (stop, steps).

    Investor: after a correction of 8%+ the stop is raised, but only once the
    stock has rallied back toward its prior high; it goes under the correction
    low, or under the 30-week MA if that sits lower while it is still rising.
    Once the MA flattens, it goes under the correction low even if that is
    above the MA (p.185-186). Trader: corrections under 7% are ignored and the
    stop sits under the correction low, never the MA (p.195-196).
    Stops only move up. steps = [(bar index, new stop), ...].
    """
    H = weekly["High"].to_numpy()
    L = weekly["Low"].to_numpy()
    C = weekly["Close"].to_numpy()
    M = ma.to_numpy()
    cpct = cfg["trader_correction_pct"] if trader else cfg["stop_correction_pct"]
    rec = cfg["trail_recover_frac"]
    lb = cfg["slope_lookback"]
    stop, steps = None, []
    peak = -np.inf
    in_corr, trough, corr_peak = False, None, None
    for i in range(max(start, 0), len(H)):
        if not in_corr:
            peak = max(peak, H[i])
            if L[i] <= peak * (1 - cpct):
                in_corr, trough, corr_peak = True, L[i], peak
        else:
            trough = min(trough, L[i])
            if C[i] >= trough + (corr_peak - trough) * rec:
                level = trough
                if not trader and i - lb >= 0 and not np.isnan(M[i]) and not np.isnan(M[i - lb]):
                    rising = (M[i] - M[i - lb]) / abs(M[i - lb]) > cfg["ma_flat_tol"]
                    if rising:
                        level = min(trough, M[i])
                cand = book_stop(level, cfg["stop_tick"])
                if stop is None or cand > stop:
                    stop = cand
                    steps.append((i, round(cand, 2)))
                in_corr = False
                peak = max(corr_peak, H[i])
    return stop, steps


def position_status(pos, weekly, ma, stages, cfg):
    """Where an open position stands this week, by the book's selling rules.

    pos: ticker, buy_date, buy_price, optional style ('investor'/'trader'),
    shares, stop (the stop the owner actually has in). Returns a dict with a
    status and the reasons, never an order: the stop remains the owner's."""
    style = str(pos.get("style") or "investor").strip().lower()
    trader = style.startswith("t")
    n = len(weekly)
    price = float(weekly["Close"].iloc[-1])
    buy_px = float(pos["buy_price"])
    bdate = pd.Timestamp(pos["buy_date"])
    after = np.where(weekly.index >= bdate)[0]
    buy_i = int(after[0]) if len(after) else n - 1
    stv = np.asarray(stages)
    # the Stage 2 run that contains (or last preceded) the purchase
    s2 = buy_i
    if stv[min(buy_i, n - 1)] == 2:
        while s2 > 0 and stv[s2 - 1] == 2:
            s2 -= 1
    else:
        s2 = max(0, buy_i - cfg["base_window"])
    start = s2
    pre = weekly["Low"].iloc[max(0, start - cfg["base_window"]):max(start, 1)]
    floor = float(pre.min()) if len(pre) else float(weekly["Low"].iloc[start])
    init = book_stop(floor, cfg["stop_tick"])
    bo_level = float(weekly["High"].iloc[max(0, start - cfg["base_window"]):max(start, 1)].max()) \
        if start > 0 else buy_px
    if trader:
        init = trader_stop(weekly, start, bo_level, cfg) or init
    trail, steps = trail_stop(weekly, ma, start, cfg, trader=trader)
    stop = max(x for x in (init, trail) if x is not None)
    # was the stop already hit since the purchase?
    hit = None
    cur = max([init] + [s for j, s in steps if j <= buy_i])
    stage_at_buy = int(stv[min(buy_i, n - 1)])
    if cur >= buy_px:                       # a stop above the purchase price means it was not a Stage 2 entry
        cur = min(init, book_stop(buy_px * 0.92, cfg["stop_tick"]))
    sched = dict(steps)
    for i in range(buy_i + 1, n):
        if weekly["Low"].iloc[i] <= cur and hit is None:
            hit = (weekly.index[i].date().isoformat(), cur)
        if i in sched:
            cur = max(cur, sched[i])
    last_stage = int(stv[-1]) if len(stv) else 0
    # a young Stage 2 whose breakout lacked the volume (p.104, p.116)
    recent_bo = bool(stage_at_buy == 2 and start > cfg["vol_base_weeks"] and n - 1 - start <= 12
                     and last_stage == 2)
    bo_heavy = True
    if recent_bo:
        bo_heavy = bool(heavy_volume(weekly["Volume"].to_numpy(dtype=float), start, cfg)["heavy"])
    ma_now = float(ma.iloc[-1])
    ext = (price / ma_now - 1) * 100 if ma_now else float("nan")
    sw = swing_target(weekly, start, price, cfg) if start > 0 else None
    your_stop = pos.get("stop")
    try:
        your_stop = float(your_stop) if your_stop not in (None, "") else None
    except (TypeError, ValueError):
        your_stop = None

    notes, status = [], "HOLD"

    if stage_at_buy != 2:
        notes.append(f"bought while the stock was in Stage {stage_at_buy}; the book buys only Stage 2 breakouts and "
                     f"pullbacks (p.129), so the stop history here is approximate.")
    if hit:
        status = "SELL - STOP HIT"
        notes.append(f"price traded through the book stop ({hit[1]:.2f}) in the week of {hit[0]}: "
                     f"sell, don't wait for a rally (p.176).")
    elif last_stage == 4:
        status = "SELL - STAGE 4"
        notes.append("Stage 4 decline: out (p.39). Stocks drop fast once they enter it.")
    elif last_stage == 3:
        if trader:
            status = "SELL - STAGE 3 TOP"
            notes.append("Stage 3 top: a trader should get out with the profit (p.36).")
        else:
            status = "SELL HALF - STAGE 3"
            notes.append("Stage 3 top: investors sell half, protect the rest with a stop under the new support (p.36-37).")
    elif trader and price < ma_now:
        status = "SELL - BELOW 30-WK MA"
        notes.append("A trader never stays with a stock that closes under its 30-week average, even by a fraction (p.196).")
    elif (not trader and recent_bo and not bo_heavy and bo_level and price < bo_level):
        status = "SELL - FAILED BREAKOUT"
        notes.append("Breakout on light volume that has fallen back under the breakout point: dump it (p.116).")
    elif recent_bo and not bo_heavy:
        status = "SELL ON FIRST RALLY"
        notes.append("The breakout did not have the required volume. The book: sell the stock on the first rally, "
                     "and dump it at once if it falls back below the breakout point (p.116).")
    elif trader and bo_level and price < bo_level and (buy_i >= start) and n - 1 - start <= 12:
        status = "SELL - BACK UNDER BREAKOUT"
        notes.append("Great trades rarely drop back below the breakout point (p.208).")
    else:
        if your_stop is not None and stop > your_stop * 1.005:
            status = "RAISE STOP"
            notes.append(f"the book's trailing method puts the stop at {stop:.2f}; yours is {your_stop:.2f}.")
        if np.isfinite(ext) and ext >= cfg["overextended_pct"]:
            if status == "HOLD":
                status = "TAKE PARTIAL - OVEREXTENDED"
            notes.append(f"{ext:.0f}% above the 30-week average: 'very overextended', lock in "
                         f"{cfg['overextended_sell_frac']} of the position and trail the rest (p.193). "
                         f"The {cfg['overextended_pct']:.0f}% trigger is not from the book.")
        if sw and trader and price >= sw["swing_target"] * 0.97:
            if status == "HOLD":
                status = "TAKE PARTIAL - SWING TARGET"
            notes.append(f"at the swing-rule target ({sw['swing_target']:.2f}): sell at least part (p.205).")
    if status == "HOLD" and not notes:
        notes.append("Stage 2, above a rising average: hold and let the stop do the work (p.186).")
    return {
        "ticker": pos["ticker"], "style": "trader" if trader else "investor",
        "buy_date": bdate.date().isoformat(), "buy_price": round(buy_px, 2),
        "price": round(price, 2),
        "gain_pct": round((price / buy_px - 1) * 100, 1),
        "stage": last_stage, "ma30": round(ma_now, 2), "pct_above_ma": round(ext, 1),
        "stop": round(float(stop), 2), "stop_pct": round((stop / price - 1) * 100, 1),
        "your_stop": your_stop, "status": status, "notes": notes,
        "swing_target": sw["swing_target"] if sw else None,
        "swing_gain_pct": sw["swing_gain_pct"] if sw else None,
        "stop_steps": [(weekly.index[i].date().isoformat(), s) for i, s in steps][-4:],
        "hit": hit,
    }


POSITION_COLS = ["ticker", "buy_date", "buy_price", "style", "stop", "sell_date", "sell_price"]


def parse_positions(text):
    """Positions from CSV text. Columns (a header row may reorder them):
    ticker, buy_date, buy_price, style (investor/trader), stop (the stop you
    actually have in), sell_date, sell_price. Share counts are deliberately not
    read: the page shows prices and percentages, never position values.
    Blank lines and lines starting with # are ignored."""
    rows, cols = [], list(POSITION_COLS)
    for raw in csv.reader(io.StringIO(text or "")):
        cells = [c.strip() for c in raw]
        if not cells or not cells[0] or cells[0].startswith("#"):
            continue
        if cells[0].lower() in ("ticker", "symbol"):
            names = {"symbol": "ticker", "date": "buy_date", "price": "buy_price",
                     "buy": "buy_price", "exit_date": "sell_date", "exit_price": "sell_price"}
            cols = [names.get(c.lower(), c.lower()) for c in cells]
            continue
        if len(cells) < 3:
            continue
        d = {cols[i]: cells[i] for i in range(min(len(cols), len(cells))) if cells[i]}
        try:
            row = {"ticker": d["ticker"].upper().replace(".", "-"),
                   "buy_date": str(pd.Timestamp(d["buy_date"]).date()),
                   "buy_price": float(d["buy_price"].replace("$", "").replace(",", ""))}
            if d.get("style"):
                row["style"] = d["style"].lower()
            if d.get("stop"):
                row["stop"] = d["stop"].replace("$", "")
            if d.get("sell_date") and d.get("sell_price"):
                row["sell_date"] = str(pd.Timestamp(d["sell_date"]).date())
                row["sell_price"] = float(d["sell_price"].replace("$", "").replace(",", ""))
        except Exception:
            continue
        rows.append(row)
    return rows


def compute_positions(rows, cfg, include_partial=False, loader=None):
    """Status for every open position, plus the closed trades. loader(ticker)
    returns daily bars; by default the price cache, then Yahoo."""
    import yfinance as yf

    def default_loader(t):
        d = uf.load_any(t)
        if d is None or d.empty:
            d = yf.download(t, period="4y", interval="1d", auto_adjust=True, progress=False)
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.droplevel(1)
        return d

    loader = loader or default_loader
    opened, closed = [], []
    for p in rows:
        if p.get("sell_date"):
            closed.append({"ticker": p["ticker"], "style": str(p.get("style") or "investor"),
                           "buy_date": p["buy_date"], "buy_price": round(p["buy_price"], 2),
                           "sell_date": p["sell_date"], "sell_price": round(p["sell_price"], 2),
                           "gain_pct": round((p["sell_price"] / p["buy_price"] - 1) * 100, 1)})
            continue
        try:
            w = completed_weekly(loader(p["ticker"]), include_partial)
            if len(w) < cfg["ma_length"] + 12:
                print(f"  position {p['ticker']}: not enough history", file=sys.stderr)
                continue
            ma = infra.moving_average(w["Close"], cfg["ma_length"], cfg["ma_type"])
            st, _ = classify(w, ma, cfg)
            r = position_status(p, w, ma, st.to_numpy(), cfg)
            r["last_bar"] = w.index[-1].date().isoformat()
            opened.append(r)
        except Exception as exc:
            print(f"  position {p['ticker']} skipped: {exc}", file=sys.stderr)
    order = {"SELL": 0, "RAISE": 1, "TAKE": 2, "HOLD": 3}
    opened.sort(key=lambda r: (order.get(r["status"].split()[0], 9), r["ticker"]))
    closed.sort(key=lambda r: r["sell_date"], reverse=True)
    return opened, closed


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
    adv_shares = float(daily["Volume"].tail(50).mean()) if daily is not None and len(daily) else 0.0
    # liquidity is judged on the LATEST four completed weeks as well as the
    # 50-day average; the lower of the two governs, so a stock that has gone
    # quiet is not treated as it was in a busier spell
    rec_sh = float(weekly["Volume"].tail(4).mean()) / 5
    rec_dollar = rec_sh * price
    if np.isfinite(rec_dollar) and rec_dollar > 0:
        if rec_dollar < avg_dollar:
            adv_shares = min(adv_shares, rec_sh) if adv_shares > 0 else rec_sh
        avg_dollar_liq = min(avg_dollar, rec_dollar)
    else:
        avg_dollar_liq = avg_dollar
    liq = ("very thin" if avg_dollar_liq < cfg["very_thin_dollar_volume"]
           else "thin" if avg_dollar_liq < cfg["thin_dollar_volume"] else "normal")
    if not np.isfinite(avg_dollar) or avg_dollar <= 0:
        return None                      # no trading at all: nothing to screen

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
    # A breakout-week volume far outside the ordinary (20x+) usually means a
    # split, relisting or corporate event rather than buying demand. Flag it,
    # and measure the pullback against the busiest ORDINARY week instead, so one
    # distorted bar cannot make every later week look "dried up".
    vol_verify = bool((bo_v["ratio"] == bo_v["ratio"] and bo_v["ratio"] >= cfg["vol_verify_mult"])
                      or (cur_v["ratio"] == cur_v["ratio"] and cur_v["ratio"] >= cfg["vol_verify_mult"]))
    if vol_verify and stage == 2:
        ordinary = []
        for j in range(start, len(weekly) - 1):
            r = heavy_volume(vol_arr, j, cfg)["ratio"]
            if r == r and r < cfg["vol_verify_mult"]:
                ordinary.append(vol_arr[j])
        if ordinary:
            peak_vol = float(max(ordinary))
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

    # --- continuation breakout (p.61): the stock "drops back close to its MA
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

    # --- was there a base before this Stage 2? (p.33, p.129) ---
    # Stage 2 follows a Stage 1 base; "don't guess a bottom... buy on breakouts
    # above resistance". Count the unbroken run of basing weeks (Stage 1, or
    # Stage 3 for a stock that topped and re-based) just before Stage 2 began.
    base_start, base_weeks_before, prior_stage = basing_run(
        stages.to_numpy(), dur, cfg["base_min_weeks"])
    # A stock that was already advancing, dipped under its MA briefly and then
    # broke out to new highs is re-starting an advance, not emerging from a
    # decline (p.61-62). The "needs a base" rule is about the latter.
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
    # rule (p.184, p.187-188) governs RAISING a stop on a stock already held, so it
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
        t_vol = bool(bo_v["spike"] and follow_ok and not vol_verify)
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
    # while the MA is up to 20" -> 19 7/8, p.184). Stops only move up.
    corr = trailed_correction_low(weekly, start, cfg["stop_correction_pct"])
    stop_trail = None
    if corr is not None:
        stop_trail = max(stop_base, book_stop(min(corr, float(ma.iloc[-1])), cfg["stop_tick"]))

    dollars = cfg["account_size"] / max(cfg["positions"], 1)
    if liq != "normal" and price and adv_shares > 0:
        # a position bigger than a few percent of a day's volume moves the stock
        dollars = min(dollars, adv_shares * cfg["thin_max_adv_pct"] / 100 * price)

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
        "vol_verify": vol_verify,
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
        "avg_dollar_vol_m": round(avg_dollar_liq / 1e6, 3),
        "adv_dollars": round(avg_dollar_liq, 0),   # lower of 50-day and latest 4 weeks
        "adv_shares": round(adv_shares, 0),
        "liq": liq,
    }
    m.update(triple)
    # exit guidance (Chapter 6): trader stop, swing-rule target, overextension
    m["overextended"] = bool(stage == 2 and m["pct_above_ma"] >= cfg["overextended_pct"])
    m["trader_stop"] = m["trader_stop_pct"] = None
    m["swing_target"] = m["swing_gain_pct"] = m["swing_peak"] = m["swing_low"] = None
    m["swing_cleared"] = None
    if stage == 2 and bo_level > 0:
        ts_ = trader_stop(weekly, bo_i, bo_level, cfg)
        if ts_ is not None and ts_ < price:
            m["trader_stop"] = round(ts_, 2)
            m["trader_stop_pct"] = round((ts_ - price) / price * 100, 1)
        sw_ = swing_target(weekly, bo_i, price, cfg)
        if sw_:
            m.update(sw_)
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
        m["stop_basis"] = "trailed stop (correction low or MA, p.184)"
        m.update({"stop": round(stop_trail, 2), "stop_pct": round(pct, 1),
                  "wide_stop": bool(pct < -cfg["wide_stop_pct"])})
    if v0 == "CONTINUATION - BUY":
        # the floor this breakout came out of is the consolidation, not the
        # original base (p.61)
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
    advance, when it is far above the ideal entry point" (p.129).
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
    # the MA "clearly trending higher. This is important!" (p.61)
    # ...and it must actually BE a consolidation -- a resistance zone, not a
    # rally that merely brushed the MA (p.61). Same width limit as any range.
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
            # out anew, that is the book's continuation buy (p.61) -- the stage
            # count merely reset when price dipped under the MA.
            return continuation_verdict() if continuation else NO_BASE_VERDICT
        if not heavy_bo:
            # "If the volume pattern is negative (not high enough on
            # breakout), sell the stock on the first rally" (p.116)
            return "SUSPECT - LOW VOLUME BREAKOUT"
        pb = m.get("pct_above_breakout")
        if pb is not None and pb > chase:
            # already far above the entry point; ~80% of initial breakouts
            # pull back toward it (p.62), so wait for that
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


def explain(m, cfg, mkt=None):
    """Rule-by-rule checklist for one stock: what passes, what fails, and why.
    Each check is [name, status, detail, page]; status is pass / fail / warn / na."""
    mkt = mkt or {}
    ck = []

    def add(name, status, detail, ref=""):
        ck.append([name, status, detail, ref])

    def f(v, d=1):
        return "n/a" if v is None or (isinstance(v, float) and v != v) else f"{v:.{d}f}"

    stage = m.get("stage")
    sw = m.get("stage_weeks") or 0
    fresh = sw <= cfg["fresh_weeks"]

    # 1. market
    if mkt.get("blocked"):
        add("Market trend", "fail", "S&P 500 or Dow is in Stage 4: no buying (p.129, p.270)", "p.129")
    else:
        add("Market trend", "pass", "S&P 500 and Dow are not in Stage 4", "p.129")

    # 2. sector
    gs, gname = m.get("group_stage"), m.get("group") or "sector"
    if gs is None:
        add("Sector", "na", "no sector read for this stock", "p.78")
    elif gs in (1, 2):
        add("Sector", "pass", f"{gname} is in Stage {gs}", "p.78")
    else:
        add("Sector", "fail", f"{gname} is in Stage {gs}: don't buy a stock in a negative group (p.129)", "p.129")

    # 3. stage
    if stage == 2:
        add("Stock stage", "pass", f"Stage 2, week {sw}", "p.34")
    elif stage == 1:
        add("Stock stage", "warn", "Stage 1: still basing. Put a buy-stop above the top of the range (p.115)", "p.115")
    elif stage == 3:
        add("Stock stage", "fail", "Stage 3 top: never buy here (p.38)", "p.38")
    elif stage == 4:
        add("Stock stage", "fail", "Stage 4 decline: never buy (p.39)", "p.39")
    else:
        add("Stock stage", "na", "not classified", "")

    # 4. 30-week average
    px, ma = m.get("price"), m.get("ma30")
    if px is not None and ma:
        pa = (px / ma - 1) * 100
        if px < ma:
            add("30-week average", "fail", f"price ${px:,.2f} is {abs(pa):.1f}% BELOW the 30-week average ${ma:,.2f} (p.129)", "p.129")
        elif m.get("ma_state") == "Falling":
            add("30-week average", "fail", f"average is declining: don't buy even above it (p.129)", "p.129")
        else:
            add("30-week average", "pass", f"price {pa:.1f}% above a {str(m.get('ma_state')).lower()} average (${ma:,.2f})", "p.14")

    # 5. base
    if stage == 2:
        bw = m.get("base_weeks_before")
        if m.get("after_advance") or (bw is not None and bw >= cfg["base_min_weeks"]):
            add("Base behind it", "pass", f"{bw} weeks of basing before this advance" if bw else "restart of an earlier advance", "p.33")
        else:
            add("Base behind it", "fail", f"only {bw} weeks of base: V-shaped, don't guess a bottom (p.129)", "p.129")

    # 6. breakout volume
    if stage == 2:
        r = m.get("bo_vol_ratio")
        rtxt = "volume not measured" if r is None or r != r else f"{r:.1f}x the prior 4 weeks"
        when = "breakout week" if fresh else f"breakout week ({sw} weeks ago)"
        how = "build-up" if m.get("bo_buildup") else "spike"
        if m.get("bo_heavy"):
            add("Breakout volume", "pass",
                f"{when}: {rtxt} ({how}); needs 2x or a 3-4 week build-up (p.104)", "p.104")
        else:
            add("Breakout volume", "fail" if fresh else "warn",
                f"{when}: {rtxt}; needs 2x or a 3-4 week build-up (p.104)", "p.104")

    if stage == 2 and m.get("vol_verify"):
        add("Volume data check", "warn",
            f"breakout volume of {f(m.get('bo_vol_ratio') or m.get('vol_ratio_4wk'), 0)}x normal is far outside the ordinary. "
            f"That often comes from a split, relisting or corporate event rather than buying. Check the chart; "
            f"pullback volume is measured against the busiest ordinary week instead", "p.104")

    # 7. relative strength
    rs = m.get("rs")
    if rs is None:
        add("Relative strength", "na", "not enough history", "p.110")
    elif rs >= 0:
        add("Relative strength", "pass", f"Mansfield RS {rs:+.1f}, above zero (p.110)", "p.110")
    elif m.get("rs_improving") and rs >= -cfg["rs_deep_negative"] and not m.get("rs_below_peak"):
        add("Relative strength", "warn", f"RS {rs:+.1f} is below zero but improving (p.110)", "p.110")
    else:
        why = ("lower than at the base's peak" if m.get("rs_below_peak")
               else "deep in negative territory" if rs < -cfg["rs_deep_negative"] else "not improving")
        add("Relative strength", "fail", f"RS {rs:+.1f}, below zero and {why} (p.113)", "p.113")

    # 8. resistance
    note = m.get("resistance_note") or ""
    if m.get("resistance_clear", True):
        add("Overhead resistance", "pass", note or "clear", "p.115")
    else:
        add("Overhead resistance", "fail", f"{note}: discard stocks with resistance nearby (p.115)", "p.115")

    # 9. entry point
    pb, bo = m.get("pct_above_breakout"), m.get("breakout_level")
    if stage == 2 and pb is not None and bo:
        if fresh:
            if pb <= cfg["max_chase_pct"]:
                add("Entry point", "pass", f"{pb:+.1f}% from the ${bo:,.2f} breakout: close to the entry (p.129)", "p.129")
            else:
                add("Entry point", "fail", f"{pb:+.1f}% above the ${bo:,.2f} breakout: too late, wait for a pullback (p.129)", "p.129")
        elif -cfg["pullback_below"] <= pb <= cfg["pullback_band"]:
            add("Entry point", "pass", f"in the pullback zone, {pb:+.1f}% from the ${bo:,.2f} breakout (p.34)", "p.34")
        elif pb > cfg["pullback_band"]:
            add("Entry point", "warn", f"{pb:+.1f}% above the ${bo:,.2f} breakout: wait for a dip back near it (p.34)", "p.34")
        else:
            add("Entry point", "fail", f"{pb:+.1f}% vs the ${bo:,.2f} breakout: slipped back under it", "p.34")

    # 10. pullback volume
    vp = m.get("vol_vs_peak")
    if stage == 2 and not fresh and pb is not None and -cfg["pullback_below"] <= pb <= cfg["pullback_band"]:
        if vp is not None and vp <= cfg["pullback_vol_peak_max"]:
            add("Pullback volume", "pass", f"{vp:.2f} of the breakout peak (needs <= {cfg['pullback_vol_peak_max']:.2f}): volume dried up (p.105)", "p.105")
        elif vp is not None:
            add("Pullback volume", "fail", f"{vp:.2f} of the breakout peak; needs <= {cfg['pullback_vol_peak_max']:.2f}, down over 75% (p.105)", "p.105")

    # 11. stop
    sp, spp, basis = m.get("stop"), m.get("stop_pct"), m.get("stop_basis")
    in_zone = (stage == 2 and not fresh and pb is not None
               and -cfg["pullback_below"] <= pb <= cfg["pullback_band"])
    if in_zone and m.get("trail_stop") is not None:
        sp, spp = m["trail_stop"], m["trail_stop_pct"]
        basis = "trailed stop under the correction low or average (p.184)"
    if stage == 2 and not fresh and pb is not None and pb > cfg["pullback_band"] \
            and m.get("verdict") not in BUY_VERDICTS:
        add("Stop within 15%", "na", "a stop is set when you buy; not meaningful this far above the entry", "p.183")
    elif stage in (1, 2) and sp is not None and spp is not None:
        if spp < -cfg["wide_stop_pct"]:
            add("Stop within 15%", "fail", f"stop ${sp:,.2f} is {spp:.1f}% away ({basis}); the book limits it to 15% (p.184)", "p.184")
        else:
            add("Stop within 15%", "pass", f"stop ${sp:,.2f}, {spp:.1f}% from price ({basis})", "p.183")

    # liquidity: no minimum in the book; thin stocks get wider order limits (p.66)
    liq, adv = m.get("liq"), m.get("adv_dollars")
    if liq == "normal" and adv:
        add("Liquidity", "pass", f"averages ${adv/1e6:,.1f}M a day: ordinary order handling", "p.66")
    elif liq in ("thin", "very thin") and adv is not None:
        add("Liquidity", "warn", f"{liq}: averages ${adv/1e3:,.0f}K a day. The book stretches the buy limit to half a point for "
            f"thinly traded stocks (p.66); expect slippage on the stop and keep the position small", "p.66")

    # 12. triple confirmation
    ts = m.get("triple_score")
    if stage == 2 and ts is not None and fresh:
        add("Triple confirmation", "pass" if ts == 3 else "na", f"{int(ts)}/3 of volume, RS turning positive, 40%+ run before breakout (p.150-152)", "p.150-152")
    return ck


def headline(bucket, m, checks):
    fails = [c for c in checks if c[1] == "fail"]
    names = ", ".join(c[0].lower() for c in fails[:3])
    if bucket == "ACTIVE BUY":
        return "Meets every rule. See the entry and stop below."
    if bucket == "NEAR MISS":
        return (f"Close. Everything passes except pullback volume ({m.get('vol_vs_peak')} of the breakout peak; "
                f"the book's example needs 0.25 or less). Watch for volume to dry up.")
    if bucket == "BUY-STOP WATCH":
        return "Coiled in a base with an acceptable stop. Not a buy until it breaks out above the top on heavy volume."
    if bucket == "SKIP - STOP TOO WIDE":
        return "A good setup, but the protective stop would be more than 15% away. The book says to skip it unless the chart is outstanding."
    if bucket == "WAIT FOR PULLBACK":
        return "A good breakout, but price is too far above the entry point. Wait for a pullback toward it."
    if bucket == "DISCARDED - RESISTANCE":
        return "Would otherwise qualify, but heavy supply sits just overhead. Re-check if it clears that level."
    if bucket == "SUSPECT BREAKOUT":
        return "New Stage 2 without the required volume surge. The book warns these often fail."
    if bucket == "BLOCKED - SECTOR":
        return "The stock itself may look fine, but its sector is in Stage 3 or 4. The book says don't buy in a negative group."
    if bucket == "NO SECTOR DATA":
        return "The stock may qualify, but its sector could not be determined. The book requires a healthy group (p.78), so it is not listed as a buy."
    if bucket == "SUSPENDED - MARKET":
        return "Buying is suspended because the S&P 500 or the Dow is in Stage 4."
    if fails:
        return f"Not a candidate right now: {names}."
    warns = [c for c in checks if c[1] == "warn"]
    if warns:
        return f"Not a buy yet: {', '.join(c[0].lower() for c in warns[:2])}. See the checklist."
    return "Not a candidate right now."


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
    p.add_argument("--time-budget", type=float, default=240.0,
                   help="minutes allowed for downloading prices before the screen runs on "
                        "whatever was fetched (default 240)")
    p.add_argument("--sector-budget", type=int, default=600,
                   help="Yahoo sector lookups per run for stocks outside the S&P tables")
    p.add_argument("--sector-file", default="data/sectors.json")
    p.add_argument("--no-breadth", action="store_true",
                   help="skip the market-breadth gauges (screens favorable groups only; faster)")
    p.add_argument("--feed", help="dashboard JSON path (default weinstein_feed.json "
                   "on full runs)")
    p.add_argument("--positions-file", default="positions.csv",
                   help="your open positions (CSV); see positions.csv")
    p.add_argument("--positions-only", action="store_true",
                   help="update only the positions file (fast; no market screen)")
    p.add_argument("--positions-out", default="positions.json")
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

    if args.positions_only:
        import json as _j, datetime as _d
        txt = open(args.positions_file).read() if os.path.exists(args.positions_file) else ""
        op, cl = compute_positions(parse_positions(txt), cfg, args.include_partial)
        bars = [r["last_bar"] for r in op if r.get("last_bar")]
        with open(args.positions_out, "w") as f:
            _j.dump({"generated": _d.datetime.now(__import__("zoneinfo").ZoneInfo("America/Chicago")).isoformat(timespec="minutes"),
                     "last_bar": max(bars) if bars else "", "positions": op, "closed": cl},
                    f, indent=1, default=str)
        print(f"positions: {len(op)} open, {len(cl)} closed -> {args.positions_out}")
        return

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

    umeta = {}
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
        # the whole listed market, as Weinstein's chart books were (p.31)
        tickers, smap, umeta = uf.build_universe(
            min_price=cfg["min_price"], prefilter_dollar_vol=0, sector_path=args.sector_file,
            sector_budget=args.sector_budget,
            deadline=time.time() + 600)
        print(f"Universe: {umeta}", file=sys.stderr)
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
    fstats = uf.ensure_prices(want, time_budget_min=args.time_budget)
    print(f"Price fetch: {fstats}", file=sys.stderr)

    # Every stock in the universe is analysed so the dashboard's ticker lookup
    # can explain stocks in unfavorable sectors too; the buy tables use only
    # the favorable-group rows (group_ok).
    scan = universe_all if (smap and not (args.tickers or args.file)) else tickers
    stale_skipped = 0
    for t in scan:
        daily = uf.load_any(t)
        if daily is None or daily.empty:
            continue
        if (uf.last_friday() - daily.index[-1].date()).days > 7 and not args.include_partial:
            stale_skipped += 1        # data too old to trust: not screened
            continue
        try:
            r = analyse(t, completed_weekly(daily, args.include_partial),
                        daily, index_weekly, cfg,
                        group_stage=groups.get(smap.get(t)))
            if r:
                r["group"] = smap.get(t, "")
                r["group_ok"] = bool(args.all or not smap or smap.get(t) in good)
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
                dd = uf.load_any(t)
                if dd is not None and not dd.empty:
                    cl[t] = dd["Close"].tail(520)
            closes = pd.DataFrame(cl)
            gauges += breadth_gauges(closes, idx["Close"])
            print(f"Breadth gauges computed on {closes.shape[1]} stocks", file=sys.stderr)
        except Exception as exc:
            print(f"  breadth skipped: {exc}", file=sys.stderr)

    df_all = pd.DataFrame(rows)
    df = df_all[df_all["group_ok"]].reset_index(drop=True)
    out_path = args.out
    if (args.tickers or args.file) and args.out == "weinstein_results.csv":
        out_path = "weinstein_check.csv"   # spot checks never overwrite the full run
    df_all.to_csv(out_path, index=False)

    if args.detail or args.tickers:
        print_detail(df)

    # "Don't buy when the overall market trend is bearish." (p.129)
    blocked = (mkt_stage == 4 or dow_stage == 4) and not args.all
    if blocked:
        print("=" * 84)
        print("MARKET (S&P 500 or Dow) IS IN STAGE 4.  Weinstein: don't buy when the overall market "
              "trend is bearish (p.129, p.270).")
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
    # Discarded for nearby overhead resistance (p.115, p.129): everything that
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

    def _entry_zone(r, kind):
        """entry zone per the book: buy the breakout (p.150), don't chase (p.129)."""
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
                "shares", "avg_dollar_vol_m", "adv_dollars", "liq", "vol_verify", "stop_basis", "bo_vol_ratio", "bo_buildup",
                "triple_vol", "triple_rs", "triple_adv", "triple_score", "rs_at_peak",
                "base_weeks_before", "range_bottom", "group_stage", "ma30", "pct_above_ma",
                "trader_stop", "trader_stop_pct", "swing_target", "swing_gain_pct",
                "swing_peak", "swing_low", "swing_cleared", "overextended")}
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
                lo, hi = _entry_zone(r, kind)
                d["entry_low"], d["entry_high"] = _clean(lo), _clean(hi)
            except Exception:
                d["entry_low"] = d["entry_high"] = None
            out.append(d)
        return out

    # ---- ticker lookup: every screened stock, with a rule-by-rule explanation ----
    _bucket = {}
    for _frame, _lab in ((suspects, "SUSPECT BREAKOUT"), (disc, "DISCARDED - RESISTANCE"),
                         (near, "NEAR MISS"), (waits, "WAIT FOR PULLBACK"),
                         (watch_skip, "SKIP - STOP TOO WIDE"), (watch, "BUY-STOP WATCH"),
                         (skips, "SKIP - STOP TOO WIDE"), (buys, "ACTIVE BUY")):
        for _t in _frame["ticker"]:
            _bucket[_t] = _lab
    _mkt = {"blocked": bool(blocked), "sp": mkt_stage, "dow": dow_stage}
    _look = {}
    for _, _r in df_all.iterrows():
        _m = {k: _clean(v) for k, v in _r.items()}
        _b = _bucket.get(_r["ticker"])
        if _b is None:
            would = (_r["verdict_no_res"] in (BUY_VERDICTS | {WAIT_VERDICT, "BUY-STOP WATCH"}))
            if not _r["group_ok"] and would:
                _b = "NO SECTOR DATA" if not _r["group"] else "BLOCKED - SECTOR"
            else:
                _b = "NOT A CANDIDATE"
        if blocked and _b in ("ACTIVE BUY", "NEAR MISS", "BUY-STOP WATCH", "WAIT FOR PULLBACK"):
            _b = "SUSPENDED - MARKET"
        _ck = explain(_m, cfg, _mkt)
        _entry = None
        if _b in ("ACTIVE BUY", "NEAR MISS", "BUY-STOP WATCH", "SKIP - STOP TOO WIDE",
                  "WAIT FOR PULLBACK", "DISCARDED - RESISTANCE"):
            _k = ("pullback" if _r["verdict"] == "PULLBACK - BUY" or _b == "NEAR MISS"
                  else "base" if _b == "BUY-STOP WATCH" else "breakout")
            try:
                _lo, _hi = _entry_zone(_r, _k)
                _entry = [_clean(_lo), _clean(_hi)]
            except Exception:
                _entry = None
        _look[_r["ticker"]] = {
            "p": _m["price"], "g": _m.get("group") or "", "b": _b,
            "h": headline(_b, _m, _ck), "c": _ck, "e": _entry,
            "s": _clean(_r["trail_stop"] if _b == "NEAR MISS" else _r["stop"]),
            "r": _clean(_r["trail_stop_pct"] if _b == "NEAR MISS" else _r["stop_pct"]),
            "v": _r["verdict"]}

    _feed = {
        "generated": _dt.datetime.now(__import__("zoneinfo").ZoneInfo("America/Chicago")).isoformat(timespec="minutes"),
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
        "coverage": {"universe": len(universe_all), "analysed": int(len(df_all)),
                     "stale_skipped": stale_skipped, **{k: v for k, v in fstats.items()},
                     **{("u_" + k): v for k, v in umeta.items()}},
    }
    _lookup_doc = {"l": _look, "u": sorted(set(universe_all))}

    # ---- open positions: where each stands by the book's selling rules ----
    _pos_text = open(args.positions_file).read() if os.path.exists(args.positions_file) else ""
    _pos_out, _pos_closed = compute_positions(parse_positions(_pos_text), cfg, args.include_partial)

    if args.feed or not (args.tickers or args.file):
        _fp = args.feed or "weinstein_feed.json"
        with open(_fp, "w") as _f:
            _json.dump(_feed, _f, indent=1)
        with open(_fp.replace(".json", "") + "_lookup.json", "w") as _f:
            _json.dump(_lookup_doc, _f, separators=(",", ":"))
        with open(_fp.replace(".json", "") + "_positions.json", "w") as _f:
            _json.dump({"generated": _feed["generated"], "last_bar": _feed["last_bar"],
                        "positions": _pos_out, "closed": _pos_closed}, _f, indent=1, default=str)

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
          f"{cfg['max_chase_pct']:.0f}% above the entry point (p.129)")
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
          f"{cfg['resistance_near_pct']:.0f}% overhead (p.115, p.129)")
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
