#!/usr/bin/env python3
"""
Stage Analysis & VCP Breakout Screener
======================================
An independent Python reimplementation of a Weinstein Stage Analysis +
Minervini VCP/Trend Template dashboard, built to screen hundreds of tickers
at once instead of clicking through them one at a time.

Run it on your own machine (it needs internet access to pull price data).

QUICK START
-----------
    pip install yfinance pandas numpy
    python3 stage_vcp_screener.py --sectors "Information Technology,Health Care"

Other examples:
    python3 stage_vcp_screener.py --universe sp500              # all of S&P 500
    python3 stage_vcp_screener.py --tickers AMZN,NVDA,MSFT      # explicit list
    python3 stage_vcp_screener.py --file my_tickers.txt         # one ticker per line
    python3 stage_vcp_screener.py --universe sp500 --all        # show every row, not just buys

Settings that mirror the TradingView inputs live in the CONFIG block below.
"""

import argparse
import gc
import logging
import os
import sys
import time
import warnings
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# yfinance logs a multi-line error for every symbol it cannot fetch, which
# buries the actual progress output on a 6000-name run. Missing tickers are
# already tracked and reported via the coverage summary.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)


def _raise_fd_limit():
    """macOS defaults to a 256 open-file soft limit, which a threaded download
    run exhausts almost immediately. The symptoms look exactly like server-side
    throttling -- connections fail, DNS lookups fail ("getaddrinfo() thread
    failed to start"), cache writes fail with [Errno 24] -- but the limit is
    local. Raise the soft limit toward the hard limit at startup.
    """
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = 8192 if hard == resource.RLIM_INFINITY else min(8192, hard)
        if soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
            return soft, want
        return soft, soft
    except Exception:
        return None, None


_FD_BEFORE, _FD_AFTER = _raise_fd_limit()

# ----------------------------------------------------------------------------
# CONFIG  (mirrors the TradingView indicator inputs)
# ----------------------------------------------------------------------------

CONFIG = {
    # --- moving average / stage engine ---
    # Weinstein used Mansfield charts, which plot a WEIGHTED 30-week MA:
    # "Mansfield charts do not give a simple 30-week MA where all 30 weeks
    # count equally. Instead, they use a weighted 30-week MA whereby the most
    # recent action counts far more than the old input" (p.25).
    "ma_type": "WMA",          # "WMA", "EMA", or "SMA" -- dashboard uses 30-WMA
    "ma_flat_tolerance": 0.002,  # slope within +/-0.2% counts as "flat", i.e.
                                 # "no longer declining" (p.24)
    "ma_length": 30,           # 30 weeks
    "rs_length": 52,           # Mansfield RS lookback (weeks)
    "rs_trend_weeks": 13,      # ~90 days, the window over which a sub-zero RS
                               # line must be improving to remain buyable (p.111)
    "rs_deep_negative": 10.0,  # RS below this is "deep in negative territory"
                               # and disqualifying regardless of trend (p.113)
    "benchmark": "^GSPC",      # SPX
    "slope_lookback": 2,       # bars back used to judge rising/falling
    "breakout_lookback": 8,    # weeks of prior highs a breakout must clear
    "chandelier_lookback": 22, # weeks used for the trailing-stop high
    "momentum_lookback": 10,   # bars back for the slope comparison. This and
                               # slope_lookback=2 come straight from the
                               # indicator's settings string ("... 2 10 ...").
    "momentum_deadband": 0.0,  # 0 = never report "Steady"; the dashboard did
                               # not show Steady on any sampled stock

    # --- liquidity filters ---
    "min_avg_volume": 0,           # min avg daily SHARE volume. OFF by
                                   # default: a share-count floor penalises
                                   # high-priced stocks. TDY averages 468k
                                   # shares but $306M/day and was being
                                   # dropped by a 500k share filter while the
                                   # dashboard rated it S2 LAUNCH - BUY.
                                   # Liquidity is judged in dollars below.
    "min_dollar_volume": 20_000_000,  # min avg daily DOLLAR volume. The
                                   # dashboard's settings string carries
                                   # 20,000,000 here -- it is what makes TILE
                                   # ($17M/day) and GTY read AVOID - ILLIQUID.
    "require_momentum": True,      # a buy needs momentum that is not Slowing.
                                   # Both dashboard buys (FTI, TFC) showed
                                   # Accelerating; every WATCH - RESISTANCE
                                   # name showed Slowing.
    "min_price": 5.0,
    "download_chunk": 100,         # tickers per yfinance batch
    "batch_pause": 1.0,            # seconds between batches (throttle relief)
    "retry_pause": 2.0,            # base backoff for retrying missing tickers
    "min_coverage_pct": 70,        # warn loudly below this download coverage
    "round_pause": 180,            # seconds between download rounds
    "cooldown_trigger_pct": 70,    # batch success below this = rate limited
    "cooldown_seconds": 60,        # wait this long (x N) for the window to reset
    "max_cooldowns": 8,            # give up cooling after this many

    # --- VCP / volume ---
    "vcp_lookback_days": 15,        # window to hunt for volume dry-up
    "vcp_dryup_ratio": 0.65,        # vol < 65% of avg = dry-up
    "vcp_contractions_req": 2,      # tightening ranges required
    "vol_avg_days": 50,
    "net_vol_days": 10,
    "vol_base_weeks": 4,            # breakout volume is judged against the
                                    # prior FOUR weeks (Weinstein p.115, p.150)
    "breakout_vol_mult": 2.0,       # "even more than twice the average trading
                                    # of the past four weeks" (p.150)
    "pullback_vol_max": 0.75,       # a proper pullback comes on contracting
                                    # volume -- GT contracted "over 75 percent
                                    # from peak levels" (p.115)

    # --- risk / position sizing ---
    "account_size": 300_000,
    "risk_pct": 1.0,                # % of account risked per trade
    "atr_length": 14,
    "atr_initial_stop": 2.0,        # initial stop multiplier (calibrated to
                                    # the TradingView dashboard: AMZN @ 271.58
                                    # -> stop 234.61, 81 shares on 1% of 300k)
    "atr_half_exit": 1.8,           # momentum (half) exit multiplier
    "atr_chandelier": 2.8,          # full exit (chandelier) multiplier
    "swing_low_weeks": 8,           # window for the prior correction low that
                                    # anchors Weinstein's structural stop
    "stop_cushion": 0.005,          # place it a shade under the level, the way
                                    # he uses 19 7/8 rather than 20 (p.184)
    "atr_extended_stop": 1.4,       # extended trailing stop, armed once the
                                    # stock is over-extended (recent high
                                    # minus this x ATR)

    # --- entry gating ---
    "max_extension_buy": 12.0,      # % above 30WMA still considered buyable
    "pullback_extension": 6.0,      # fallback when no breakout level is known
    "pullback_band": 8.0,           # how far above the breakout price still
                                    # counts as "close to the breakout point"
    "pullback_below_bo": 3.0,       # tolerance for dipping slightly under it
                                    # ("the less it pulls back, the more
                                    # strength it is showing", p.34)
    "fresh_launch_weeks": 4,        # Stage 2 duration considered a fresh launch

    # --- episodic pivot (news-driven gap launch; bypasses extension gates) ---
    "episodic_max_weeks": 5,        # must be a fresh Stage 2 entry. TDY was
                                    # rated S2 LAUNCH - BUY at 5 weeks.
    "episodic_max_off_high": -6.0,  # must be within this % of the 52w high
    "episodic_vol_ratio": 1.5,      # weekly or launch volume multiple required
    "resistance_prox": 1.0,         # % below the 52w high that still counts as
                                    # "at the highs"; below this there is
                                    # overhead supply
    "pullback_max_weeks": 5,        # a pullback buy is only valid early in the
                                    # Stage 2 run. FTI(2w)/TFC(3w) were buys;
                                    # FNB(8w)/KEY(8w)/MIDD(7w)/EXPD(13w) were
                                    # all WATCH - RESISTANCE.
    "strict_trend_template": True,  # FAIL on trend template => AVOID

    # --- extended trailing stop / exhaustion (spec from the indicator author) ---
    "exhaustion_ratio": 1.0,        # arm at the historical average peak extension
    "min_hist_runs": 2,             # need this many prior Stage 2 runs to trust it
    "min_meaningful_peak": 15.0,    # FLOOR on that average, so small blips
                                    # cannot arm the stop early
    "exhaustion_rsi": 70.0,         # weekly RSI must also be overheated
    "extension_hard_ceiling": 30.0, # ...unless extension reaches this, which
                                    # arms the stop regardless of RSI
    "rsi_length": 14,
}

SECTOR_ETFS = {
    "Energy": "XLE", "Materials": "XLB", "Industrial": "XLI",
    "Cons Disc": "XLY", "Staples": "XLP", "Health": "XLV",
    "Finance": "XLF", "Tech": "XLK", "Telecom": "XLC",
    "Utility": "XLU", "Real Estate": "XLRE",
}

# yfinance reports sectors with its own names; map them onto the ETF panel.
YF_SECTOR_MAP = {
    "Technology": "Tech",
    "Healthcare": "Health",
    "Financial Services": "Finance",
    "Consumer Cyclical": "Cons Disc",
    "Consumer Defensive": "Staples",
    "Energy": "Energy",
    "Basic Materials": "Materials",
    "Industrials": "Industrial",
    "Utilities": "Utility",
    "Real Estate": "Real Estate",
    "Communication Services": "Telecom",
}

# Same mapping for the GICS names used by the S&P 500 constituent table.
GICS_SECTOR_MAP = {
    "Information Technology": "Tech",
    "Health Care": "Health",
    "Financials": "Finance",
    "Consumer Discretionary": "Cons Disc",
    "Consumer Staples": "Staples",
    "Energy": "Energy",
    "Materials": "Materials",
    "Industrials": "Industrial",
    "Utilities": "Utility",
    "Real Estate": "Real Estate",
    "Communication Services": "Telecom",
}

# Concurrency for price downloads. Small on purpose -- see _download_batch.
DOWNLOAD_THREADS = 4

# "yahoo" (bulk, fast, throttles hard) or "stooq" (per-ticker, no API key,
# tolerates sustained requests far better).
DATA_SOURCE = "yahoo"

# Non-common securities to strip out of the symbol directory. Warrants, rights
# and units are what produce the "$XXXXW: possibly delisted" noise.
# Whole-word patterns for securities that are not ordinary shares. (Plain
# substring matching wrongly dropped "Copyright", "Wright", "Bright Horizons",
# "Citizens United" and every "American Depositary Share" such as Nokia.)
EXCLUDE_NAME_REGEX = (
    r"\bwarrants?\b", r"\brights?\b", r"\bunits?\b", r"\bpreferred\b",
    r"\bdepositary shares?,? each representing (a |an )?(1/|one[- ])?\d*[/-]?\w* ?(interest|fractional)",
    r"\b1/\d+(th)?\b", r"% note", r"\bnotes? due\b", r"\bdebentures?\b",
    r"\bsenior notes?\b", r"\bsubordinated\b", r"\bconvertible\b",
    r"\bwhen issued\b", r"\btest stock\b",
    # blank-check shells (SPACs): their price is pinned to the cash in trust, so
    # a "Stage 2 breakout" is meaningless. Not from the book; OURS.
    r"\bacquisition (corp|corporation|co|company|holdings?|ltd|limited|inc)\b",
    r"\bblank check\b", r"\bcantor equity partners\b",
)
EXCLUDE_NAME_PATTERNS = EXCLUDE_NAME_REGEX      # kept for older imports

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# The S&P Composite 1500 (large + mid + small cap). Each Wikipedia table also
# carries the GICS sector, which lets us filter to Stage 2 sectors BEFORE
# downloading any price history -- the single biggest speed win available.
SP_INDEX_PAGES = [
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
]


# ----------------------------------------------------------------------------
# INDICATOR PRIMITIVES
# ----------------------------------------------------------------------------

def moving_average(series: pd.Series, length: int, kind: str) -> pd.Series:
    kind = kind.upper()
    if kind == "EMA":
        return series.ewm(span=length, adjust=False).mean()
    if kind == "WMA":
        weights = np.arange(1, length + 1)
        return series.rolling(length).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    return series.rolling(length).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's smoothing (TradingView's RMA)."""
    return series.ewm(alpha=1 / length, adjust=False).mean()


def atr(df: pd.DataFrame, length: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return rma(tr, length)


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's RSI (RMA-smoothed), matching the indicator's '14 RMA' input."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100.0).where(avg_loss != 0, 100.0)


def mansfield_rs(stock_close: pd.Series, index_close: pd.Series, length: int) -> pd.Series:
    """Mansfield Relative Strength: how far the stock/index ratio sits above
    its own moving average. Positive = outperforming."""
    ratio = stock_close / index_close.reindex(stock_close.index).ffill()
    return ((ratio / ratio.rolling(length).mean()) - 1) * 100


# ----------------------------------------------------------------------------
# STAGE ENGINE
# ----------------------------------------------------------------------------

def classify_stages_simple(close: pd.Series, ma: pd.Series, lookback: int) -> pd.Series:
    """Stateless four-quadrant classification (price vs MA, MA slope).

    Kept as a fallback for series where no High data is available. It cannot
    see a top forming while the MA is still rising -- use classify_stages()
    whenever OHLC is available.
    """
    rising = ma > ma.shift(lookback)
    above = close > ma

    stage = pd.Series(np.nan, index=close.index)
    stage[above & rising] = 2
    stage[above & ~rising] = 1
    stage[~above & rising] = 3
    stage[~above & ~rising] = 4
    return stage


def classify_stages(df: pd.DataFrame, ma: pd.Series, cfg: dict) -> pd.Series:
    """Weinstein stage classification as a STATE MACHINE.

    A stateless "price above a rising MA = Stage 2" test cannot detect a top
    while the 30-week MA is still rising -- a stock 15% off its high with a
    rolled-over trend still scores Stage 2. This tracks the trend's life cycle
    instead:

      Stage 1 (Basing)      recovering, no breakout yet
      Stage 2 (Advancing)   entered only on a genuine breakout above the prior
                            N-week high with price over a rising MA
      Stage 3 (Topping)     Stage 2 was lost -- price fell under the MA or the
                            chandelier trailing stop -- but the decline has not
                            confirmed. Requires a NEW breakout to regain Stage 2.
      Stage 4 (Declining)   price below a falling MA
    """
    close, high = df["Close"], df["High"]
    lookback = cfg["slope_lookback"]

    rising = (ma > ma.shift(lookback)).to_numpy()
    above = (close > ma).to_numpy()

    # Weinstein's entry test is that the MA is "no longer declining" -- not
    # that it is already rising: "the ideal time to buy is when it breaks out
    # above resistance and also moves above its 30-week MA, which must no
    # longer be declining" (p.24). In Stage 1 the MA "loses its downside slope
    # and starts to flatten out" (p.33), so demanding a rising MA rejects
    # exactly the Stage 1 -> Stage 2 breakouts the method is built around.
    # A small tolerance band treats a flat MA as not declining.
    ma_prev = ma.shift(lookback)
    not_declining = ((ma - ma_prev) / ma_prev.abs().replace(0, np.nan)
                     >= -cfg["ma_flat_tolerance"]).fillna(False).to_numpy()

    # prior N-week high: the level a real breakout must clear
    prior_high = high.rolling(cfg["breakout_lookback"]).max().shift(1).to_numpy()

    # chandelier trailing stop that keeps a Stage 2 run alive
    atr_w = atr(df, cfg["atr_length"])
    chand = (high.rolling(cfg["chandelier_lookback"]).max()
             - atr_w * cfg["atr_chandelier"]).to_numpy()

    c = close.to_numpy()
    ma_v = ma.to_numpy()
    out = np.full(len(close), np.nan)
    bo_level = np.full(len(close), np.nan)   # breakout price of the current run
    state = None
    prev_state = None

    for i in range(len(close)):
        if np.isnan(ma_v[i]):
            continue

        breakout = (not np.isnan(prior_high[i])) and c[i] > prior_high[i]

        if state is None:                      # seed from the quadrant
            state = 2 if (above[i] and rising[i]) else (
                4 if (not above[i] and not rising[i]) else (
                    1 if above[i] else 3))
        elif state == 2:
            if not above[i] and not rising[i]:
                state = 4
            elif (not above[i]) or (not np.isnan(chand[i]) and c[i] < chand[i]):
                state = 3                      # trend broke -> topping
        elif state == 3:
            if not above[i] and not rising[i]:
                state = 4
            elif above[i] and not_declining[i] and breakout:
                state = 2                      # must break out again
        elif state == 4:
            if above[i] and not_declining[i] and breakout:
                state = 2
            elif above[i]:
                state = 1                      # basing
        elif state == 1:
            if above[i] and not_declining[i] and breakout:
                state = 2
            elif not above[i] and not rising[i]:
                state = 4

        # Record the level that was cleared on entry to Stage 2. Weinstein's
        # second buy point is a dip back toward this breakout price (p.34),
        # so it has to be carried forward for the life of the run.
        if state == 2:
            if prev_state != 2:
                bo_level[i] = prior_high[i]
            else:
                bo_level[i] = bo_level[i - 1] if i else np.nan
        prev_state = state

        out[i] = state

    stages = pd.Series(out, index=close.index)
    stages.attrs["breakout_level"] = pd.Series(bo_level, index=close.index)
    return stages


def classify_momentum(ma: pd.Series, cfg: dict) -> str:
    """Rate of change of the 30-week MA slope: Accelerating / Steady / Slowing.

    Averages the per-bar slope over two adjacent windows instead of sampling
    two single points, which is far less noisy. The comparison is scaled by
    the size of the slopes involved, so it behaves correctly when the MA is
    turning up from flat or negative (small absolute numbers, big change in
    character) as well as in fast trends.
    """
    lb = cfg["slope_lookback"]         # 2 in the dashboard's settings
    mb = cfg["momentum_lookback"]      # 10 in the dashboard's settings
    clean = ma.dropna()
    if len(clean) < mb + lb + 2:
        return "Steady"

    # Slope measured over `lb` bars, compared with the same measure taken `mb`
    # bars ago. This mirrors the indicator's own inputs. An earlier attempt
    # averaged two adjacent 8-week windows instead; it agreed with the
    # dashboard on only 2 of 8 sampled stocks, so it has been reverted.
    def slope_at(end):
        a, b = clean.iloc[end], clean.iloc[end - lb]
        return (a - b) / b if b else 0.0

    now = slope_at(-1)
    prior = slope_at(-1 - mb)

    band = cfg["momentum_deadband"]
    if band and abs(now - prior) < band:
        return "Steady"
    return "Accelerating" if now >= prior else "Slowing"


def stage_duration(stages: pd.Series) -> int:
    """Consecutive bars the current stage has held."""
    if stages.dropna().empty:
        return 0
    current = stages.iloc[-1]
    count = 0
    for val in stages.iloc[::-1]:
        if val == current:
            count += 1
        else:
            break
    return count


def stage2_extension_stats(close: pd.Series, ma: pd.Series, stages: pd.Series,
                           min_run_weeks: int = 4):
    """Peak extension in the CURRENT Stage 2 run, and the average peak
    extension across all PRIOR Stage 2 runs (the stock's 'personality').

    Short 1-3 week Stage 2 blips inside a choppy base are ignored, otherwise
    they drag the historical average down and produce false exhaustion calls.
    """
    ext = ((close - ma) / ma) * 100
    in_s2 = (stages == 2).fillna(False)

    runs, current = [], []
    for i, flag in enumerate(in_s2):
        if flag:
            current.append(ext.iloc[i])
        elif current:
            runs.append(current)
            current = []

    current_peak = max(current) if current else np.nan
    hist_peaks = [max(r) for r in runs if len(r) >= min_run_weeks]
    avg_hist_peak = float(np.mean(hist_peaks)) if hist_peaks else np.nan
    return current_peak, avg_hist_peak, len(hist_peaks)


# ----------------------------------------------------------------------------
# VCP / VOLUME
# ----------------------------------------------------------------------------

def detect_vcp(daily: pd.DataFrame, cfg: dict) -> tuple:
    """Volatility Contraction Pattern: successively tighter pullbacks paired
    with a volume dry-up. Returns (detected: bool, contractions: int)."""
    look = cfg["vcp_lookback_days"]
    if len(daily) < look + cfg["vol_avg_days"]:
        return False, 0

    recent = daily.tail(look)
    avg_vol = daily["Volume"].tail(cfg["vol_avg_days"]).mean()
    dryup = (recent["Volume"].tail(5).mean() < avg_vol * cfg["vcp_dryup_ratio"])

    # count tightening swings: each successive high-to-low range smaller
    window = max(3, look // 3)
    ranges = []
    for i in range(0, look, window):
        chunk = recent.iloc[i:i + window]
        if len(chunk) >= 2:
            ranges.append((chunk["High"].max() - chunk["Low"].min()) / chunk["Close"].mean())

    contractions = sum(1 for a, b in zip(ranges, ranges[1:]) if b < a)
    detected = bool(dryup and contractions >= cfg["vcp_contractions_req"])
    return detected, contractions


def net_volume_metrics(daily: pd.DataFrame, cfg: dict) -> tuple:
    """10-day net volume surge and the duel of peak up/down volume spikes."""
    n = cfg["net_vol_days"]
    if len(daily) < n + cfg["vol_avg_days"]:
        return np.nan, np.nan, np.nan

    avg_vol = daily["Volume"].tail(cfg["vol_avg_days"]).mean()
    recent = daily.tail(n).copy()
    recent["chg"] = recent["Close"].diff().fillna(0)

    up_vol = recent.loc[recent["chg"] > 0, "Volume"].sum()
    dn_vol = recent.loc[recent["chg"] < 0, "Volume"].sum()
    net_surge = (up_vol - dn_vol) / avg_vol / n

    up_spikes = recent.loc[recent["chg"] > 0, "Volume"]
    dn_spikes = recent.loc[recent["chg"] < 0, "Volume"]
    peak_up = up_spikes.max() / avg_vol if not up_spikes.empty else np.nan
    peak_dn = dn_spikes.max() / avg_vol if not dn_spikes.empty else np.nan
    return net_surge, peak_up, peak_dn


def stage2_launch_volume(weekly: pd.DataFrame, stages: pd.Series, cfg: dict) -> float:
    """Volume multiple on the week the stock first entered the current Stage 2."""
    in_s2 = (stages == 2).fillna(False)
    if not in_s2.iloc[-1]:
        return np.nan

    idx = len(in_s2) - 1
    while idx > 0 and in_s2.iloc[idx - 1]:
        idx -= 1

    if idx < 10:
        return np.nan
    base_avg = weekly["Volume"].iloc[max(0, idx - cfg["vol_base_weeks"]):idx].mean()
    if not base_avg or np.isnan(base_avg):
        return np.nan
    return weekly["Volume"].iloc[idx] / base_avg


# ----------------------------------------------------------------------------
# MINERVINI TREND TEMPLATE
# ----------------------------------------------------------------------------

def trend_template(daily: pd.DataFrame, rs_value: float) -> tuple:
    """Price > 50MA > 150MA > 200MA, 200MA rising, well off the 52w low,
    near the 52w high, positive relative strength."""
    if len(daily) < 260:
        return False, ["insufficient history"]

    close = daily["Close"]
    ma50 = close.rolling(50).mean().iloc[-1]
    ma150 = close.rolling(150).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    ma200_prior = close.rolling(200).mean().iloc[-22]
    price = close.iloc[-1]
    # intraday extremes, consistent with the 52-week proximity metric
    low52 = daily["Low"].tail(252).min()
    high52 = daily["High"].tail(252).max()

    # NOTE: relative strength is deliberately NOT part of this test. The
    # dashboard passes NVDA on the Trend Template while showing RS at -0.16,
    # so its TT is the pure moving-average structure check. Weak RS is handled
    # separately as its own veto in new_entry_verdict().
    checks = {
        "price>MA50": price > ma50,
        "MA50>MA150": ma50 > ma150,
        "MA150>MA200": ma150 > ma200,
        "MA200 rising": ma200 > ma200_prior,
        "30%+ off 52w low": price >= low52 * 1.30,
        "within 25% of 52w high": price >= high52 * 0.75,
    }
    failed = [k for k, v in checks.items() if not v]
    return len(failed) == 0, failed


# ----------------------------------------------------------------------------
# VERDICTS
# ----------------------------------------------------------------------------

def is_exhausted(m: dict, cfg: dict) -> bool:
    """Has the extended trailing stop been triggered?

    Per the indicator author, this is a two-part test: the stock must be
    stretched beyond the average peak extension of its own prior Stage 2 runs
    AND weekly RSI must be above 70. A stock can stretch a long way above its
    moving average while RSI peaks in the 60s, which is why RNG kept reading as
    a launch at 35% extension.

    Two safeguards on top of that:
      * the historical average is floored (small blips must not arm the stop)
      * a hard ceiling arms the stop regardless, so a runaway move cannot give
        back an unlimited amount of profit
    """
    ext = m["current_ext_pct"]

    # Hard ceiling -- overrides everything, including the RSI requirement.
    if ext >= cfg["extension_hard_ceiling"]:
        return True

    peak = m["avg_hist_peak"]
    if peak is None or (isinstance(peak, float) and np.isnan(peak)):
        return False
    if m.get("hist_s2_runs", 0) < cfg["min_hist_runs"]:
        return False

    # Floor the historical average rather than ignoring small ones.
    peak = max(float(peak), cfg["min_meaningful_peak"])
    if ext < peak * cfg["exhaustion_ratio"]:
        return False

    # Second half of the test: momentum must actually be overheated.
    r = m.get("weekly_rsi")
    if r is None or (isinstance(r, float) and np.isnan(r)):
        return False
    return r > cfg["exhaustion_rsi"]


def is_fresh_launch(m: dict, cfg: dict) -> bool:
    """A stock in the first weeks of Stage 2, breaking out at the highs on
    heavy volume -- the dashboard's S2 LAUNCH - BUY.

    Critically this bypasses the extension ceiling AND the exhaustion guard.
    A genuine launch is extended the moment it appears; that is what a launch
    IS. RNG is the reference case: the dashboard calls it S2 LAUNCH - BUY at
    35.4% above the 30WMA against an 18.8% historical peak, because the run is
    days old rather than exhausted.
    """
    if m["ticker_stage"] != 2:
        return False
    if m["stage2_weeks"] > cfg["episodic_max_weeks"]:
        return False
    # must be launching AT the highs, not into overhead supply
    if m["high52_prox_pct"] < cfg["episodic_max_off_high"]:
        return False
    # must be outperforming
    if (m["rs_vs_market"] or 0) <= 0:
        return False
    # must carry real volume
    vol_ok = ((m["weekly_vol_ratio"] or 0) >= cfg["episodic_vol_ratio"]
              or (m["s2_launch_net_vol"] or 0) >= cfg["episodic_vol_ratio"])
    if not vol_ok:
        return False
    # NOTE: up-volume spikes are deliberately NOT required to exceed down
    # spikes. TDY was rated S2 LAUNCH - BUY with spikes of 2.6x up against
    # 2.7x down, so the dashboard does not treat that as disqualifying.
    return True


def new_entry_verdict(m: dict, cfg: dict) -> str:
    stage = m["ticker_stage"]
    ext = m["current_ext_pct"]
    dur = m["stage2_weeks"]

    if stage == 3:
        return "AVOID - TOPPING"
    if stage == 4:
        return "AVOID - STAGE 4"

    # Dollar-volume liquidity gate, shown as a verdict rather than silently
    # dropped -- matches the dashboard's AVOID - ILLIQUID.
    if cfg["min_dollar_volume"]:
        if (m.get("avg_dollar_vol_m") or 0) * 1e6 < cfg["min_dollar_volume"]:
            return "AVOID - ILLIQUID"
    if stage == 1:
        return "WATCH - BASING"
    if cfg["strict_trend_template"] and not m["trend_template_pass"]:
        return "AVOID - TT FAIL"
    # Weinstein does NOT treat sub-zero RS as an automatic veto: "Don't think,
    # however, that you can never buy a stock below the zero RS line... If the
    # relative strength is in good shape and improving and all other criteria
    # are positive, then go for it" (p.120). What he rejects is RS that is deep
    # in negative territory and still trending down (p.113).
    rs_now = m.get("rs_vs_market")
    if rs_now is not None and rs_now < 0:
        improving = bool(m.get("rs_improving"))
        if not improving or rs_now < -cfg["rs_deep_negative"]:
            return "AVOID - WEAK RS"

    # squat: broke to new highs this week then reversed hard into the close
    if m["squat"]:
        return "WARNING - SQUAT"

    # The hard extension ceiling outranks even a fresh launch -- it is the
    # author's safety net against giving back an unlimited amount of profit.
    if m["current_ext_pct"] >= cfg["extension_hard_ceiling"]:
        return "TOO LATE - EXTENDED"

    # Otherwise checked BEFORE the extension, exhaustion and momentum gates --
    # those exist for orderly, ageing trends and would always veto a fresh
    # launch, which is the setup we most want to catch.
    if is_fresh_launch(m, cfg):
        return "S2 LAUNCH - BUY"

    # Extension vs. the stock's own historical peak personality.
    # Only trusted when there are at least 2 meaningful prior Stage 2 runs and
    # the historical peak is a realistic magnitude -- otherwise a quiet stock
    # gets flagged exhausted at a trivially small extension.
    if is_exhausted(m, cfg):
        return "TOO LATE - EXTENDED"

    fresh = dur <= cfg["fresh_launch_weeks"]

    # Decelerating 30WMA slope disqualifies an ESTABLISHED trend (an early
    # Stage 3 warning) -- MIDD/KEY/FNB/BGC were all 8-18 weeks in and Slowing
    # while the dashboard said WATCH. It must NOT be applied to a fresh launch:
    # the dashboard read RNG as Accelerating where this script reads Slowing,
    # and gating a days-old breakout on a 16-week slope comparison is wrong.
    if cfg["require_momentum"] and not fresh and m.get("momentum") == "Slowing":
        return "WATCH - MOMENTUM SLOWING"
    heavy_vol = (m["weekly_vol_ratio"] or 0) >= cfg["breakout_vol_mult"]
    launch_vol = (m["s2_launch_net_vol"] or 0) >= cfg["breakout_vol_mult"]

    # Overhead supply: a stock well into Stage 2 that still has not made a new
    # high has trapped buyers above it. The dashboard calls this
    # WATCH - RESISTANCE, and it is the single biggest remaining source of
    # false buys -- FNB(8w), KEY(8w), MIDD(7w) and EXPD(13w) were all flagged
    # this way while FTI(2w), TFC(3w) and RNG(1w) were genuine buys.
    under_resistance = m["high52_prox_pct"] < -cfg["resistance_prox"]
    early = dur <= cfg["pullback_max_weeks"]

    if fresh and (heavy_vol or launch_vol) and ext <= cfg["max_extension_buy"]:
        return "S2 LAUNCH - BUY"
    if m["vcp_detected"] and ext <= cfg["max_extension_buy"] and not (
            under_resistance and not early):
        return "VCP PIVOT - BUY"
    if fresh and (m["net_vol_surge"] or 0) > 1.0 and ext <= cfg["max_extension_buy"]:
        return "EPISODIC PIVOT"
    if under_resistance and not early:
        return "WATCH - RESISTANCE"
    # Weinstein's second buy point: the dip back toward the breakout price, on
    # contracting volume (p.34, p.115). Measured against the breakout level
    # when it is known, falling back to distance above the 30WMA when it is not.
    quiet = (m["weekly_vol_ratio"] or 9) <= cfg["pullback_vol_max"]
    near_bo = m.get("pct_above_breakout")
    if near_bo is not None and not (isinstance(near_bo, float)
                                    and np.isnan(near_bo)):
        in_buy_zone = -cfg["pullback_below_bo"] <= near_bo <= cfg["pullback_band"]
    else:
        in_buy_zone = ext <= cfg["pullback_extension"]
    if in_buy_zone and quiet:
        return "BUY ZONE PULLBACK"
    if under_resistance:
        return "WATCH - RESISTANCE"
    if ext > cfg["max_extension_buy"]:
        return "WATCH - EXTENDED"
    return "S2 TRENDING"


def active_trade_verdict(m: dict, cfg: dict) -> str:
    stage = m["ticker_stage"]
    if stage == 4:
        return "SELL ALL"
    if stage == 3:
        return "EXIT - STAGE 3"
    if stage == 1:
        return "REDUCE / TIGHTEN"
    if is_exhausted(m, cfg):
        return "REDUCE / TIGHTEN"
    if m["momentum"] == "Slowing":
        return "HOLD - WATCH MOMENTUM"
    return "HOLD POSITION"


# ----------------------------------------------------------------------------
# PER-TICKER ANALYSIS
# ----------------------------------------------------------------------------

def analyze(ticker, weekly, daily, index_weekly, cfg, reject_log=None):
    """Compute the full dashboard for one ticker. Returns a dict, or None if
    the ticker fails a data/liquidity precondition (reason appended to
    reject_log when supplied)."""

    def reject(reason):
        if reject_log is not None:
            reject_log.append((ticker, reason))
        return None

    if weekly is None or daily is None or len(weekly) < cfg["ma_length"] + 10:
        return reject(f"only {0 if weekly is None else len(weekly)} weekly bars "
                      f"(need {cfg['ma_length'] + 10})")
    if len(daily) < 260:
        return reject(f"only {len(daily)} daily bars (need 260)")

    close_w = weekly["Close"]
    price = float(close_w.iloc[-1])
    if price < cfg["min_price"]:
        return reject(f"price ${price:.2f} below ${cfg['min_price']} minimum")

    avg_volume = float(daily["Volume"].tail(50).mean())
    if avg_volume < cfg["min_avg_volume"]:
        return reject(f"avg volume {avg_volume:,.0f} below "
                      f"{cfg['min_avg_volume']:,.0f} minimum")

    avg_dollar_vol = float((daily["Close"] * daily["Volume"]).tail(50).mean())
    if cfg["min_dollar_volume"] and avg_dollar_vol < cfg["min_dollar_volume"]:
        return reject(f"avg daily $vol ${avg_dollar_vol/1e6:.1f}M below "
                      f"${cfg['min_dollar_volume']/1e6:.0f}M minimum")

    # --- stage engine ---
    ma = moving_average(close_w, cfg["ma_length"], cfg["ma_type"])
    ma_now = float(ma.iloc[-1])
    stages = classify_stages(weekly, ma, cfg)
    stage = int(stages.iloc[-1]) if not np.isnan(stages.iloc[-1]) else 0

    ma_rising = bool(ma.iloc[-1] > ma.iloc[-1 - cfg["slope_lookback"]])
    momentum = classify_momentum(ma, cfg)

    # Distance back to the breakout price -- Weinstein's second buy point is a
    # dip "back close to the breakout point" (p.34), not a fixed distance above
    # the moving average.
    bo_series = stages.attrs.get("breakout_level")
    bo_level = float(bo_series.iloc[-1]) if bo_series is not None else np.nan
    if bo_level and not np.isnan(bo_level) and bo_level > 0:
        dist_to_breakout = ((price - bo_level) / bo_level) * 100
    else:
        bo_level, dist_to_breakout = np.nan, np.nan

    current_ext = ((price - ma_now) / ma_now) * 100
    peak_ext, avg_hist_peak, hist_run_count = stage2_extension_stats(close_w, ma, stages)
    s2_weeks = stage_duration(stages) if stage == 2 else 0

    # --- weekly RSI (second half of the extended-stop test) ---
    rsi_series = rsi(close_w, cfg["rsi_length"])
    rsi_now = float(rsi_series.iloc[-1]) if len(rsi_series) else np.nan

    # --- relative strength ---
    rs_series = mansfield_rs(close_w, index_weekly["Close"], cfg["rs_length"])
    rs_val = float(rs_series.iloc[-1]) if not np.isnan(rs_series.iloc[-1]) else np.nan
    # "the RS line was trending higher for 90 days prior to the breakout" (p.111)
    rs_lb = cfg["rs_trend_weeks"]
    rs_improving = bool(len(rs_series.dropna()) > rs_lb
                        and rs_val > float(rs_series.iloc[-1 - rs_lb]))

    # --- 52 week high proximity ---
    # Intraday HIGH, not closing high. Using closes made every stock look
    # closer to its high than it is (GKOS read -1.1% where the dashboard
    # showed -9.4%, because GKOS spiked intraday but never closed there).
    high52 = float(daily["High"].tail(252).max())
    high52_prox = ((price - high52) / high52) * 100

    # --- volume / VCP ---
    vcp_detected, contractions = detect_vcp(daily, cfg)
    net_surge, peak_up, peak_dn = net_volume_metrics(daily, cfg)
    launch_vol = stage2_launch_volume(weekly, stages, cfg)
    # Weinstein measures breakout volume against the PRIOR FOUR WEEKS, not a
    # longer window: "The average weekly volume for the prior four weeks was
    # approximately 22,000, but on the breakout week it was almost triple that
    # figure" (p.115), and "volume of even more than twice the average trading
    # of the past four weeks" (p.150). A 10-week baseline dilutes the surge.
    n = cfg["vol_base_weeks"]
    wk_vol_avg = weekly["Volume"].iloc[-(n + 1):-1].mean()
    weekly_vol_ratio = (float(weekly["Volume"].iloc[-1] / wk_vol_avg)
                        if wk_vol_avg else np.nan)

    # --- squat detection: pierced the prior high then closed back down ---
    wk = weekly.iloc[-1]
    prior_high = float(weekly["High"].iloc[-9:-1].max())
    week_range = float(wk["High"] - wk["Low"])
    squat = bool(
        wk["High"] > prior_high
        and week_range > 0
        and (wk["Close"] - wk["Low"]) / week_range < 0.4
    )

    # --- ATR stops & sizing ---
    atr_w = atr(weekly, cfg["atr_length"])
    atr_now = float(atr_w.iloc[-1])
    initial_stop = price - atr_now * cfg["atr_initial_stop"]
    initial_stop_pct = ((initial_stop - price) / price) * 100
    # --- Weinstein's own structural stop -------------------------------
    # He places the initial stop "under the prior correction low before the
    # breakout" (p.186), then trails it beneath each successive correction low
    # -- and "if the correction low is above the rising 30-week MA, place the
    # sell-stop below the MA" (p.184). This is a price-structure stop, not the
    # ATR arithmetic the dashboard uses, so it is reported alongside.
    swing_lb = cfg["swing_low_weeks"]
    recent_low = float(weekly["Low"].iloc[-(swing_lb + 1):-1].min()) \
        if len(weekly) > swing_lb else float(weekly["Low"].min())
    weinstein_stop = max(recent_low, ma_now) if stage == 2 else recent_low
    weinstein_stop *= (1 - cfg["stop_cushion"])

    recent_high = float(weekly["High"].tail(cfg["chandelier_lookback"]).max())
    half_exit = price - atr_now * cfg["atr_half_exit"]
    chandelier = recent_high - atr_now * cfg["atr_chandelier"]

    # Stage 2 stop is normally the 30WMA. Once the extended stop is armed it
    # tightens to recent high - 1.4 ATR, which is what the dashboard shows.
    # Verified on RNG: high 58.98, ATR 6.035 -> 50.53, against a 30WMA of 41.07.
    stage2_stop = ma_now
    extended_stop = recent_high - atr_now * cfg["atr_extended_stop"]

    risk_per_share = price - initial_stop
    risk_dollars = cfg["account_size"] * (cfg["risk_pct"] / 100)
    shares = int(risk_dollars / risk_per_share) if risk_per_share > 0 else 0
    position_value = round(shares * price, 2)

    # --- trend template ---
    tt_pass, tt_failed = trend_template(daily, rs_val)

    m = {
        "ticker": ticker,
        "price": round(price, 2),
        "ticker_stage": stage,
        "stage2_weeks": s2_weeks,
        "ma30": round(ma_now, 2),
        "ma30_trend": "Rising" if ma_rising else "Falling",
        "momentum": momentum,
        "current_ext_pct": round(current_ext, 1),
        "breakout_level": round(bo_level, 2) if not np.isnan(bo_level) else None,
        "pct_above_breakout": (round(dist_to_breakout, 1)
                               if not np.isnan(dist_to_breakout) else None),
        "peak_s2_ext": round(peak_ext, 1) if not np.isnan(peak_ext) else np.nan,
        "avg_hist_peak": round(avg_hist_peak, 1) if not np.isnan(avg_hist_peak) else np.nan,
        "hist_s2_runs": hist_run_count,
        "high52_prox_pct": round(high52_prox, 1),
        "rs_vs_market": round(rs_val, 1) if not np.isnan(rs_val) else None,
        "weekly_rsi": round(rsi_now, 1) if not np.isnan(rsi_now) else None,
        "rs_improving": rs_improving,
        "vcp_detected": vcp_detected,
        "vcp_contractions": contractions,
        "s2_launch_net_vol": round(launch_vol, 2) if launch_vol and not np.isnan(launch_vol) else None,
        "weekly_vol_ratio": round(weekly_vol_ratio, 2) if not np.isnan(weekly_vol_ratio) else None,
        "net_vol_surge": round(net_surge, 2) if not np.isnan(net_surge) else None,
        "peak_spike_up": round(peak_up, 1) if not np.isnan(peak_up) else None,
        "peak_spike_dn": round(peak_dn, 1) if not np.isnan(peak_dn) else None,
        "squat": squat,
        "stage2_stop": round(stage2_stop, 2),
        "extended_stop": round(extended_stop, 2),
        "weinstein_stop": round(weinstein_stop, 2),
        "weinstein_stop_pct": round((weinstein_stop - price) / price * 100, 2),
        "extended_stop_armed": False,   # set below, once exhaustion is known
        "half_exit_mom": round(half_exit, 2),
        "full_exit_chan": round(chandelier, 2),
        "initial_stop": round(initial_stop, 2),
        "initial_stop_pct": round(initial_stop_pct, 2),
        "suggested_shares": shares,
        "position_value": position_value,
        "trend_template_pass": tt_pass,
        "trend_template_failed": ", ".join(tt_failed) if tt_failed else "",
        "avg_volume": int(avg_volume),
        "avg_dollar_vol_m": round(avg_dollar_vol / 1e6, 1),
    }
    # Arm the extended stop, and let it replace the Stage 2 stop when tighter.
    if is_exhausted(m, cfg):
        m["extended_stop_armed"] = True
        m["stage2_stop"] = round(max(stage2_stop, extended_stop), 2)

    m["new_entry"] = new_entry_verdict(m, cfg)
    m["active_trade"] = active_trade_verdict(m, cfg)
    return m


# ----------------------------------------------------------------------------
# DATA LOADING
# ----------------------------------------------------------------------------

def load_all_us_listed():
    """Every common stock listed on NASDAQ / NYSE / AMEX.

    Uses Nasdaq Trader's public symbol directory. ETFs, test issues, warrants,
    units and preferreds are excluded so the screen only sees ordinary shares.
    """
    print("Fetching full US listed universe...", file=sys.stderr)
    frames = []

    def clean(df, sym_col, name_col):
        df = df[df["Test Issue"] == "N"]
        if "ETF" in df.columns:
            df = df[df["ETF"] != "Y"]
        # Drop warrants / rights / units / preferreds by security name -- far
        # more reliable than guessing from the 5th letter of the symbol.
        import re
        rx = re.compile("|".join(EXCLUDE_NAME_REGEX))
        names = df[name_col].astype(str).str.lower()
        mask = ~names.apply(lambda n: bool(rx.search(n)))
        df = df[mask]
        return df[[sym_col]].rename(columns={sym_col: "sym"})

    nq = pd.read_csv(NASDAQ_LISTED, sep="|")
    frames.append(clean(nq, "Symbol", "Security Name"))

    ot = pd.read_csv(OTHER_LISTED, sep="|")
    frames.append(clean(ot, "ACT Symbol", "Security Name"))

    syms = pd.concat(frames)["sym"].dropna().astype(str)
    # drop the trailing "File Creation Time" footer row
    syms = syms[~syms.str.contains(r"File Creation|\$", regex=True)]
    # Yahoo writes share classes with a dash (BRK.B -> BRK-B). Preferreds and
    # depositary shares were already removed by security name above, so any
    # dot left here is a genuine class marker worth keeping.
    syms = syms.str.strip().str.replace(".", "-", regex=False)
    syms = sorted(set(
        s for s in syms
        if s and s.isascii() and len(s) <= 6
        and all(ch.isalpha() or ch == "-" for ch in s)))
    print(f"  {len(syms)} common stocks after filtering", file=sys.stderr)
    return syms


def read_html_tables(url, timeout=30):
    """Read HTML tables from a URL with a real User-Agent.

    pandas.read_html uses urllib's default UA, which Wikipedia answers with
    HTTP 403 Forbidden. Fetching via requests with a browser UA avoids that.
    """
    import io as _io
    try:
        import requests
        resp = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/125.0 Safari/537.36",
                     "Accept-Language": "en-US,en;q=0.9"})
        resp.raise_for_status()
        return pd.read_html(_io.StringIO(resp.text))
    except ImportError:
        # requests missing -- fall back to pandas and hope the host allows it
        return pd.read_html(url)


def _find_col(cols, *wanted):
    """Wikipedia renames these columns periodically; match loosely."""
    for w in wanted:
        for c in cols:
            if w in str(c).lower():
                return c
    return None


def load_sp1500():
    """S&P Composite 1500 constituents with their GICS sector.

    ~1500 names covering essentially every US stock liquid enough to matter for
    this strategy, and the sector arrives for free -- no per-ticker lookups.
    """
    print("Fetching S&P 1500 constituents...", file=sys.stderr)
    sector_map = {}
    for url in SP_INDEX_PAGES:
        try:
            for table in read_html_tables(url):
                sym_c = _find_col(table.columns, "symbol", "ticker")
                sec_c = _find_col(table.columns, "gics sector", "sector")
                if sym_c is None or sec_c is None:
                    continue
                for sym, sec in zip(table[sym_c], table[sec_c]):
                    sym = str(sym).strip().upper().replace(".", "-")
                    if sym and sym != "NAN":
                        sector_map[sym] = GICS_SECTOR_MAP.get(str(sec).strip(),
                                                              str(sec).strip())
                break
        except Exception as exc:
            print(f"  could not read {url.rsplit('/', 1)[-1]}: {exc}",
                  file=sys.stderr)

    print(f"  {len(sector_map)} constituents", file=sys.stderr)
    return sorted(sector_map), sector_map


def load_universe(args):
    """Resolve the ticker list from the requested source."""
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()], {}

    if args.file:
        with open(args.file) as fh:
            tickers = [line.strip().upper() for line in fh if line.strip()
                       and not line.startswith("#")]
        return tickers, {}

    if args.universe == "all":
        return load_all_us_listed(), {}

    if args.universe == "sp1500":
        return load_sp1500()

    # S&P 500 constituents from Wikipedia (includes GICS sector)
    print("Fetching S&P 500 constituents...", file=sys.stderr)
    table = read_html_tables(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
    table["Symbol"] = table["Symbol"].str.replace(".", "-", regex=False)
    sector_map = {k: GICS_SECTOR_MAP.get(v, v)
                  for k, v in zip(table["Symbol"], table["GICS Sector"])}

    if args.sectors:
        wanted = [s.strip().lower() for s in args.sectors.split(",")]
        table = table[table["GICS Sector"].str.lower().apply(
            lambda s: any(w in s for w in wanted))]
        if table.empty:
            print("No sectors matched. Available GICS sectors:", file=sys.stderr)
            for s in sorted(GICS_SECTOR_MAP):
                print("   ", s, file=sys.stderr)
            sys.exit(1)

    return table["Symbol"].tolist(), sector_map


# Nasdaq Trader exchange codes -> TradingView prefixes
_TV_EXCHANGE = {"N": "NYSE", "A": "AMEX", "P": "AMEX", "Z": "CBOE",
                "V": "NASDAQ"}


def build_exchange_map():
    """ticker -> TradingView exchange prefix, from Nasdaq's symbol directory.

    TradingView's watchlist import wants NYSE:XOM / NASDAQ:RNG rather than a
    bare ticker, so the exchange has to come from somewhere. Two cheap HTTP
    requests cover the whole US market.
    """
    out = {}
    try:
        nq = pd.read_csv(NASDAQ_LISTED, sep="|")
        for s in nq["Symbol"].dropna().astype(str):
            out[s.strip().upper()] = "NASDAQ"
    except Exception as exc:
        print(f"  exchange lookup (nasdaq) failed: {exc}", file=sys.stderr)
    try:
        ot = pd.read_csv(OTHER_LISTED, sep="|")
        for s, ex in zip(ot["ACT Symbol"].dropna().astype(str),
                         ot["Exchange"].astype(str)):
            out.setdefault(s.strip().upper(),
                           _TV_EXCHANGE.get(ex.strip(), "NYSE"))
    except Exception as exc:
        print(f"  exchange lookup (other) failed: {exc}", file=sys.stderr)
    return out


def write_tv_watchlist(tickers, path, exchange_map=None):
    """Write a TradingView-importable watchlist (.txt, comma separated).

    TradingView caps a watchlist at 1000 symbols, so longer lists are split
    into numbered files.
    """
    exchange_map = exchange_map or {}
    syms = []
    for t in tickers:
        # yfinance writes share classes as BRK-B; TradingView expects BRK.B
        tv = t.replace("-", ".")
        ex = exchange_map.get(t.upper())
        syms.append(f"{ex}:{tv}" if ex else tv)

    written = []
    chunks = [syms[i:i + 1000] for i in range(0, len(syms), 1000)] or [[]]
    for n, chunk in enumerate(chunks, 1):
        p = path if len(chunks) == 1 else path.replace(".txt", f"_{n}.txt")
        with open(p, "w") as fh:
            fh.write(",".join(chunk))
        written.append(p)
    return written


def fetch_sectors(tickers, workers=8):
    """Look up each ticker's sector. Only called for the small set of buy
    candidates, since it costs one request per symbol."""
    from concurrent.futures import ThreadPoolExecutor
    import yfinance as yf

    def one(t):
        try:
            info = yf.Ticker(t).get_info()
            return t, YF_SECTOR_MAP.get(info.get("sector"), info.get("sector"))
        except Exception:
            return t, None

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for t, sec in pool.map(one, tickers):
            out[t] = sec
    return out


CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".price_cache")


def _cache_file(ticker):
    return os.path.join(CACHE_DIR, f"{ticker}.pkl")


def is_cached(ticker, max_age_hours=20):
    """Cheap freshness check -- no unpickling, just the file timestamp."""
    try:
        age = (time.time() - os.path.getmtime(_cache_file(ticker))) / 3600
        return age <= max_age_hours
    except OSError:
        return False


def load_cached(ticker, max_age_hours=20):
    """Return a cached daily frame if it is recent enough, else None."""
    path = _cache_file(ticker)
    try:
        age = (time.time() - os.path.getmtime(path)) / 3600
        if age > max_age_hours:
            return None
        return pd.read_pickle(path)
    except Exception:
        return None


_CACHE_WARNED = False


def save_cached(ticker, df):
    """Persist a daily frame. Failures are reported once -- a silently broken
    cache makes every retry re-download the same data."""
    global _CACHE_WARNED
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.to_pickle(_cache_file(ticker))
    except Exception as exc:
        if not _CACHE_WARNED:
            _CACHE_WARNED = True
            print(f"  WARNING: cache write failed ({exc}). Retries will be "
                  f"much slower. Try --cache-dir /tmp/price_cache",
                  file=sys.stderr)


def _to_weekly(df):
    return df.resample("W-FRI").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna()


def _stooq_one(ticker, session):
    """Daily OHLCV for one ticker from Stooq's free CSV endpoint."""
    import io as _io
    sym = ticker.lower().replace("-", "-") + ".us"
    url = f"https://stooq.com/q/d/l/?s={sym}&i=d"
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200 or not r.text.startswith("Date"):
            return ticker, None
        df = pd.read_csv(_io.StringIO(r.text), parse_dates=["Date"])
        if df.empty:
            return ticker, None
        df = df.set_index("Date").sort_index()
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return ticker, (df if not df.empty else None)
    except Exception:
        return ticker, None


def _download_batch_stooq(tickers, period, quiet):
    """Stooq has no bulk endpoint, so fetch in parallel with modest workers.
    It is far more tolerant of sustained requests than Yahoo's free tier."""
    from concurrent.futures import ThreadPoolExecutor
    import requests

    out = {}
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        with ThreadPoolExecutor(max_workers=DOWNLOAD_THREADS) as pool:
            for t, df in pool.map(lambda x: _stooq_one(x, session), tickers):
                if df is not None and not df.empty:
                    out[t] = (_to_weekly(df), df)
    return out


def _download_batch(tickers, period, quiet):
    """Dispatch to the configured data source."""
    if DATA_SOURCE == "stooq":
        return _download_batch_stooq(tickers, period, quiet)
    return _download_batch_yahoo(tickers, period, quiet)


def _download_batch_yahoo(tickers, period, quiet):
    """One bulk request; returns {ticker: (weekly, daily)} for whatever worked."""
    import yfinance as yf
    out = {}
    try:
        # threads is deliberately a SMALL int, never True. yfinance spawns one
        # thread per ticker when True, which exhausts the OS thread/DNS pool on
        # a big universe ("getaddrinfo() thread failed to start").
        data = yf.download(tickers, period=period, interval="1d",
                           group_by="ticker", auto_adjust=True,
                           progress=not quiet, threads=DOWNLOAD_THREADS)
    except Exception as exc:
        print(f"  batch download failed: {exc}", file=sys.stderr)
        return out

    if data is None or len(data) == 0:
        return out

    for t in tickers:
        try:
            df = _extract_ticker_frame(data, t)
            if df is None:
                continue
            df = df.dropna()
            if df.empty:
                continue
            out[t] = (_to_weekly(df), df)
        except Exception:
            continue
    return out


def fetch_prices(tickers, period="4y", quiet=False, retries=3, pause=2.0,
                 use_cache=True, stats=None):
    """Fetch daily bars, preferring the local cache, with retries.

    Yahoo throttles aggressively on large runs and answers with EMPTY frames
    rather than an error, so a silent partial download looks like a clean one.
    Anything missing is retried in smaller batches with a growing pause, and
    everything fetched is cached so an interrupted run resumes cheaply.
    """
    out = {}
    to_fetch = []

    if use_cache:
        for t in tickers:
            df = load_cached(t)
            if df is not None and not df.empty:
                out[t] = (_to_weekly(df), df)
            else:
                to_fetch.append(t)
    else:
        to_fetch = list(tickers)

    if stats is not None:
        stats["cached"] = len(out)

    if not to_fetch:
        return out

    fresh = _download_batch(to_fetch, period, quiet)
    for t, (_, daily) in fresh.items():
        save_cached(t, daily)
    out.update(fresh)
    missing = [t for t in to_fetch if t not in out]

    for attempt in range(retries):
        if not missing:
            break
        wait = pause * (2 ** attempt)
        if not quiet:
            print(f"    retrying {len(missing)} in {wait:.0f}s...",
                  file=sys.stderr)
        time.sleep(wait)

        size = max(10, len(missing) // (2 ** (attempt + 1)) or 10)
        recovered = {}
        for i in range(0, len(missing), size):
            recovered.update(_download_batch(missing[i:i + size], period, True))
        # BUG FIX: recovered tickers were not being cached, so every retry
        # re-downloaded work that had already succeeded.
        for t, (_, daily) in recovered.items():
            save_cached(t, daily)
        out.update(recovered)
        missing = [t for t in missing if t not in out]

    return out


def _extract_ticker_frame(data: pd.DataFrame, ticker: str):
    """Pull one ticker's OHLCV frame out of a yfinance download.

    yfinance returns different column shapes depending on how many tickers
    were requested and which version is installed: a plain OHLCV frame, a
    (ticker, field) MultiIndex, or a (field, ticker) MultiIndex. Handle all
    three rather than assuming one -- assuming a plain frame for single-ticker
    downloads is what silently dropped every result.
    """
    required = {"Open", "High", "Low", "Close", "Volume"}

    if not isinstance(data.columns, pd.MultiIndex):
        return data if required.issubset(set(data.columns)) else None

    level0 = set(data.columns.get_level_values(0))
    level1 = set(data.columns.get_level_values(1))

    if ticker in level0:
        return data[ticker].copy()
    if ticker in level1:
        return data.xs(ticker, axis=1, level=1).copy()
    if required.issubset(level0):
        return data.droplevel(1, axis=1).copy()
    return None


def sector_stage_table(cfg):
    """Weinstein stage for each sector ETF -- the 'US Sector' panel."""
    import yfinance as yf
    tickers = list(SECTOR_ETFS.values())
    data = yf.download(tickers, period="3y", interval="1wk",
                       group_by="ticker", auto_adjust=True, progress=False)
    rows = []
    for name, etf in SECTOR_ETFS.items():
        try:
            frame = _extract_ticker_frame(data, etf).dropna()
            ma = moving_average(frame["Close"], cfg["ma_length"], cfg["ma_type"])
            stages = classify_stages(frame, ma, cfg)
            rows.append((name, etf, int(stages.iloc[-1])))
        except Exception:
            rows.append((name, etf, None))
    return rows


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

BUY_SIGNALS = {"S2 LAUNCH - BUY", "VCP PIVOT - BUY", "EPISODIC PIVOT",
               "BUY ZONE PULLBACK"}


def main():
    global DOWNLOAD_THREADS, DATA_SOURCE, CACHE_DIR
    p = argparse.ArgumentParser(description="Stage Analysis & VCP screener")
    p.add_argument("--universe", default="sp1500",
                   choices=["sp1500", "sp500", "all"],
                   help="sp1500 = S&P Composite 1500 (default, fast, sectors "
                        "known up front); sp500 = large caps only; "
                        "all = every listed US stock (slow)")
    p.add_argument("--min-volume", type=float,
                   help="min average daily share volume (default 500000)")
    p.add_argument("--no-sector-filter", action="store_true",
                   help="do not require the stock's sector to be in Stage 2")
    p.add_argument("--workers", type=int, default=8,
                   help="parallel requests for sector lookup")
    p.add_argument("--chunk", type=int,
                   help="tickers per download batch (lower = more reliable)")
    p.add_argument("--pause", type=float,
                   help="seconds to wait between batches")
    p.add_argument("--threads", type=int,
                   help=f"download concurrency (default {DOWNLOAD_THREADS}; "
                        f"lower this if you see getaddrinfo errors)")
    p.add_argument("--source", choices=["yahoo", "stooq"], default="yahoo",
                   help="price data source. yahoo is fast but throttles hard "
                        "on big runs; stooq is slower per ticker but far more "
                        "reliable at scale")
    p.add_argument("--cache-dir", help="where to store cached price data")
    p.add_argument("--max-new", type=int, default=150,
                   help="max NEW tickers to download per round (default 150; "
                        "0 = unlimited)")
    p.add_argument("--rounds", type=int, default=6,
                   help="how many download rounds to run, pausing between "
                        "them to let Yahoo's limit reset (default 6)")
    p.add_argument("--round-pause", type=int,
                   help="seconds between rounds (default 180)")
    p.add_argument("--sectors", help="comma-separated GICS sectors to include")
    p.add_argument("--tickers", help="comma-separated explicit ticker list")
    p.add_argument("--file", help="path to a file with one ticker per line")
    p.add_argument("--all", action="store_true",
                   help="output every ticker, not just buy candidates")
    p.add_argument("--mode", choices=["shortlist", "signals"],
                   default="shortlist",
                   help="shortlist = every structurally sound Stage 2 name "
                        "(default, favours recall); signals = only names that "
                        "clear every buy rule (fewer, but misses more)")
    p.add_argument("--max-ext", type=float, default=15.0,
                   help="shortlist: max %% above the 30WMA (default 15)")
    p.add_argument("--max-age", type=int, default=12,
                   help="shortlist: max weeks in Stage 2 (default 12)")
    p.add_argument("--limit", type=int, default=0,
                   help="shortlist: cap the number of rows shown")
    p.add_argument("--watchlist", default="tv_watchlist.txt",
                   help="file to write the TradingView-importable list to")
    p.add_argument("--no-watchlist", action="store_true",
                   help="skip writing the TradingView watchlist file")
    p.add_argument("--detail", action="store_true",
                   help="show full diagnostics for the buy candidates")
    p.add_argument("--quiet", action="store_true",
                   help="suppress the market and sector stage context")
    p.add_argument("--out", default="screen_results.csv")
    p.add_argument("--account", type=float, help="override account size")
    p.add_argument("--risk", type=float, help="override risk %% per trade")
    p.add_argument("--ma-type", choices=["WMA", "EMA", "SMA"],
                   help="override the 30-week MA type")
    p.add_argument("--no-strict", action="store_true",
                   help="do not auto-reject on Trend Template failure")
    args = p.parse_args()

    if args.threads:
        DOWNLOAD_THREADS = max(1, args.threads)
    DATA_SOURCE = args.source
    if args.cache_dir:
        CACHE_DIR = os.path.expanduser(args.cache_dir)
    if args.round_pause is not None:
        CONFIG["round_pause"] = args.round_pause
    print(f"Data source: {DATA_SOURCE}", file=sys.stderr)
    if _FD_BEFORE is not None and _FD_AFTER != _FD_BEFORE:
        print(f"Open-file limit raised: {_FD_BEFORE} -> {_FD_AFTER}",
              file=sys.stderr)
    elif _FD_BEFORE is not None and _FD_BEFORE < 1024:
        print(f"WARNING: open-file limit is only {_FD_BEFORE} and could not be "
              f"raised. Downloads may fail with [Errno 24].\n"
              f"  Run:  ulimit -n 8192   then retry.", file=sys.stderr)

    cfg = dict(CONFIG)
    if args.account:
        cfg["account_size"] = args.account
    if args.risk:
        cfg["risk_pct"] = args.risk
    if args.ma_type:
        cfg["ma_type"] = args.ma_type
    if args.no_strict:
        cfg["strict_trend_template"] = False
    if args.min_volume:
        cfg["min_avg_volume"] = args.min_volume
    if args.chunk:
        cfg["download_chunk"] = args.chunk
    if args.pause is not None:
        cfg["batch_pause"] = args.pause

    # A spot-check of one or two tickers must not overwrite the results of a
    # full universe run -- that is how the 854-row CSV got replaced by a
    # single RNG row.
    if (args.tickers or args.file) and args.out == "screen_results.csv":
        args.out = "screen_check.csv"
        print(f"(spot check -> writing {args.out}, leaving "
              f"screen_results.csv intact)", file=sys.stderr)

    tickers, sector_map = load_universe(args)
    if not tickers:
        print("\n*** Could not build the ticker universe. ***\n"
              "The constituent list failed to download -- this is a source\n"
              "problem, not a market result.\n"
              "  - check your internet connection\n"
              "  - make sure 'requests' is installed: pip3 install requests\n"
              "  - or supply your own list:  --file my_tickers.txt\n"
              "  - or try:  --universe all\n", file=sys.stderr)
        sys.exit(1)
    print(f"Universe: {len(tickers)} tickers", file=sys.stderr)

    # market + sector context
    import yfinance as yf
    idx = yf.download(cfg["benchmark"], period="4y", interval="1d",
                      auto_adjust=True, progress=False)
    if isinstance(idx.columns, pd.MultiIndex):
        idx.columns = idx.columns.droplevel(1)
    index_weekly = idx.resample("W-FRI").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum"}).dropna()

    idx_ma = moving_average(index_weekly["Close"], cfg["ma_length"], cfg["ma_type"])
    idx_stage = classify_stages(index_weekly, idx_ma, cfg).iloc[-1]

    # Which sectors are in Stage 2 -- candidates outside these get dropped.
    sector_stages = sector_stage_table(cfg)
    stage2_sectors = {n for n, _, st in sector_stages if st == 2}

    if not args.quiet:
        print(f"\nMarket (SPX): Stage {int(idx_stage)}", file=sys.stderr)
        print(f"Stage 2 sectors: "
              f"{', '.join(sorted(stage2_sectors)) if stage2_sectors else 'none'}",
              file=sys.stderr)

    if not stage2_sectors and not args.no_sector_filter:
        print("\nNo sectors are in Stage 2 -- nothing to buy by this method.",
              file=sys.stderr)
        return

    # ---- if sectors are known up front, drop non-Stage-2 names BEFORE
    # ---- downloading anything. This is the single biggest speed win.
    if sector_map and not args.no_sector_filter:
        before = len(tickers)
        tickers = [t for t in tickers
                   if sector_map.get(t) in stage2_sectors]
        print(f"\nSector pre-filter: {before} -> {len(tickers)} tickers "
              f"in Stage 2 sectors", file=sys.stderr)
        if not tickers:
            print("Nothing left to screen.", file=sys.stderr)
            return

    # ================= PHASE 1: fill the cache =================
    # Yahoo serves small requests happily but cuts off after a few hundred in a
    # session. So acquisition is separated from analysis: each run tops up the
    # cache by a bounded amount, and analysis always runs over everything
    # cached so far. An interrupted run never loses ground.
    missing = [t for t in tickers if not is_cached(t)]
    have = len(tickers) - len(missing)
    print(f"\nCache: {have}/{len(tickers)} tickers already stored",
          file=sys.stderr)

    if missing:
        budget = args.max_new if args.max_new else len(missing)
        rounds = max(1, args.rounds)
        target = min(len(missing), budget * rounds)
        print(f"Fetching up to {target} new tickers "
              f"({rounds} round(s) of {budget})...", file=sys.stderr)

        chunk = min(cfg["download_chunk"], budget)
        fetched = 0
        queue = list(missing)

        for rnd in range(rounds):
            if not queue:
                break
            if rnd:
                print(f"  --- round {rnd + 1}: pausing "
                      f"{cfg['round_pause']}s to let the limit reset ---",
                      file=sys.stderr)
                time.sleep(cfg["round_pause"])

            round_got = 0
            while queue and round_got < budget:
                batch = queue[:chunk]
                bstats = {}
                prices = fetch_prices(batch, quiet=True,
                                      pause=cfg["retry_pause"], stats=bstats)
                queue = queue[len(batch):]
                round_got += len(batch)
                fetched += len(prices)
                pct = len(prices) / max(len(batch), 1) * 100
                print(f"  fetched {len(prices)}/{len(batch)} ({pct:.0f}%)   "
                      f"total new: {fetched}   remaining: {len(queue)}",
                      file=sys.stderr)
                # Release sockets/handles that yfinance leaves dangling before
                # starting the next batch -- this is what drives the open-file
                # count up over a long run.
                del prices
                gc.collect()

                if pct < cfg["cooldown_trigger_pct"]:
                    print(f"  low success rate -- stopping this round early",
                          file=sys.stderr)
                    break
                time.sleep(cfg["batch_pause"])

        still_missing = [t for t in tickers if not is_cached(t)]
        if still_missing:
            print(f"\n{len(still_missing)} tickers still missing. "
                  f"Run the same command again to continue "
                  f"(cached data is reused).", file=sys.stderr)

    # ================= PHASE 2: analyse everything cached =================
    available = [t for t in tickers if is_cached(t)]
    print(f"\nAnalysing {len(available)}/{len(tickers)} tickers "
          f"({len(available) / max(len(tickers), 1) * 100:.0f}% of universe)",
          file=sys.stderr)

    results, rejects = [], []
    for i in range(0, len(available), 200):
        for t in available[i:i + 200]:
            daily = load_cached(t)
            if daily is None or daily.empty:
                continue
            try:
                row = analyze(t, _to_weekly(daily), daily, index_weekly, cfg,
                              reject_log=rejects)
                if row:
                    row["sector"] = sector_map.get(t, "")
                    results.append(row)
            except Exception as exc:
                print(f"  skip {t}: {exc}", file=sys.stderr)

    coverage = len(available) / max(len(tickers), 1) * 100
    if tickers and coverage < cfg["min_coverage_pct"]:
        print(f"\n*** PARTIAL SCREEN: only {coverage:.0f}% of the universe has "
              f"data. ***\n"
              f"Results below cover just that portion -- run again to fill in "
              f"the rest.\n", file=sys.stderr)

    if rejects:
        if args.detail or args.all:
            print(f"\nFiltered out {len(rejects)} ticker(s):", file=sys.stderr)
            for t, reason in rejects[:15]:
                print(f"  {t}: {reason}", file=sys.stderr)
            if len(rejects) > 15:
                print(f"  ...and {len(rejects) - 15} more", file=sys.stderr)
        else:
            print(f"Filtered out {len(rejects)} on data/liquidity "
                  f"(--detail to list).", file=sys.stderr)

    if not results:
        print("\nNo tickers passed the data/liquidity filters.", file=sys.stderr)
        return

    df = pd.DataFrame(results)
    order = ["ticker", "sector", "price", "ticker_stage", "stage2_weeks",
             "ma30_trend", "momentum", "current_ext_pct", "peak_s2_ext",
             "avg_hist_peak", "high52_prox_pct", "rs_vs_market", "vcp_detected",
             "s2_launch_net_vol", "weekly_vol_ratio", "net_vol_surge",
             "peak_spike_up", "peak_spike_dn", "trend_template_pass",
             "initial_stop", "initial_stop_pct", "stage2_stop", "half_exit_mom",
             "full_exit_chan", "suggested_shares", "position_value",
             "new_entry", "active_trade", "trend_template_failed",
             "avg_dollar_vol_m"]
    df = df[[c for c in order if c in df.columns]]

    df["_rank"] = df["new_entry"].apply(
        lambda v: 0 if v in BUY_SIGNALS else 1)
    df = df.sort_values(["_rank", "rs_vs_market"], ascending=[True, False]).drop(columns="_rank")

    df.to_csv(args.out, index=False)

    buys = df[df["new_entry"].isin(BUY_SIGNALS)].copy()

    # ---- keep only candidates whose SECTOR is also in Stage 2 ----
    if not buys.empty and not args.no_sector_filter:
        missing = [t for t, s in zip(buys["ticker"], buys["sector"]) if not s]
        if missing:
            print(f"Looking up sectors for {len(missing)} candidates...",
                  file=sys.stderr)
            found = fetch_sectors(missing, workers=args.workers)
            buys["sector"] = [s or found.get(t) or ""
                              for t, s in zip(buys["ticker"], buys["sector"])]
            df["sector"] = [found.get(t, s) if not s else s
                            for t, s in zip(df["ticker"], df["sector"])]
            df.to_csv(args.out, index=False)

        before = len(buys)
        buys = buys[buys["sector"].isin(stage2_sectors)]
        dropped = before - len(buys)
        if dropped:
            print(f"Dropped {dropped} not in a Stage 2 sector.", file=sys.stderr)

    print(f"Screened {len(df)} -> {len(buys)} buy signals\n", file=sys.stderr)

    # ================= SHORTLIST MODE (default) =================
    # Recall first. Every Stage 2 name that is structurally sound goes on the
    # list; nothing is rejected for being "exhausted", "slowing" or "under
    # resistance", because those judgements are exactly where this script
    # disagrees with the dashboard and where TDY-style false negatives came
    # from. The final call is made in TradingView.
    if args.mode == "shortlist" and not args.detail and not args.all:
        s = df[df["ticker_stage"] == 2].copy()
        n0 = len(s)
        if not args.no_strict:
            s = s[s["trend_template_pass"] == True]          # noqa: E712
        s = s[s["rs_vs_market"].fillna(-1) > 0]
        s = s[s["stage2_weeks"] <= args.max_age]
        # The extension cap keeps tired, over-stretched trends off the list --
        # but a brand-new launch is extended by nature (RNG was 35% above its
        # 30WMA in week 1 and the dashboard called it S2 LAUNCH - BUY), so
        # fresh names are exempt.
        fresh_mask = s["stage2_weeks"] <= cfg["episodic_max_weeks"]
        s = s[(s["current_ext_pct"] <= args.max_ext) | fresh_mask]
        # ...but nothing past the hard ceiling, launch or not.
        s = s[s["current_ext_pct"] < cfg["extension_hard_ceiling"]]

        if s.empty:
            print("No Stage 2 candidates passed the structural filters.")
            return

        # freshest trends first, then strongest relative strength
        s["_buy"] = s["new_entry"].isin(BUY_SIGNALS).astype(int)
        s = s.sort_values(["_buy", "stage2_weeks", "rs_vs_market"],
                          ascending=[False, True, False]).drop(columns="_buy")
        if args.limit:
            s = s.head(args.limit)

        print(f"STAGE 2 SHORTLIST -- {len(s)} of {n0} Stage 2 names "
              f"(market: SPX Stage {int(idx_stage)})\n")
        hdr = (f"{'TICKER':<7}{'SECTOR':<13}{'PRICE':>9}  {'S2w':>4}"
               f"{'EXT%':>7}{'52W%':>7}{'RS':>7}  {'VCP':<4} SIGNAL")
        print(hdr)
        print("-" * len(hdr))
        for _, r in s.iterrows():
            print(f"{r['ticker']:<7}{str(r['sector'])[:12]:<13}"
                  f"{r['price']:>9.2f}  {int(r['stage2_weeks']):>4}"
                  f"{r['current_ext_pct']:>7.1f}{r['high52_prox_pct']:>7.1f}"
                  f"{(r['rs_vs_market'] or 0):>7.1f}  "
                  f"{'yes' if r['vcp_detected'] else '-':<4} {r['new_entry']}")
        print(f"\nSorted freshest-first. Full metrics in {args.out}.")
        print("SIGNAL is a hint only -- confirm each in TradingView.")

        # TradingView-importable watchlist
        if not args.no_watchlist:
            exmap = build_exchange_map()
            files = write_tv_watchlist(list(s["ticker"]), args.watchlist, exmap)
            print(f"\nTradingView watchlist: {', '.join(files)}")
            print("  In TradingView: open the Watchlist panel, click the list "
                  "name -> 'Import list...' and choose that file.")
        return

    # ---- signals mode: only names that cleared every buy rule ----
    if not args.detail and not args.all:
        if buys.empty:
            print("No buy signals.")
            return
        width = max(len(t) for t in buys["ticker"])
        swidth = max([len(str(s)) for s in buys["sector"]] + [6])
        for _, r in buys.iterrows():
            print(f"{r['ticker']:<{width}}  ${r['price']:<8}  "
                  f"{str(r['sector']):<{swidth}}  {r['new_entry']}")
        print(f"\n{len(buys)} to check in TradingView. "
              f"Details in {args.out}.")
        return

    # ---- --detail / --all: full diagnostics ----
    print("Verdict breakdown:", file=sys.stderr)
    for verdict, count in df["new_entry"].value_counts().items():
        mark = "*" if verdict in BUY_SIGNALS else " "
        print(f" {mark} {verdict:<22} {count}", file=sys.stderr)
    print("", file=sys.stderr)

    show = df if args.all else buys
    if show.empty:
        print("No buy candidates this week. Re-run with --all to see why.")
        return

    # A single ticker reads far better as a vertical panel that lines up with
    # the TradingView dashboard than as a very wide one-row table.
    if len(show) == 1:
        print_dashboard(show.iloc[0])
        return

    cols = ["ticker", "sector", "price", "ticker_stage", "stage2_weeks",
            "current_ext_pct", "high52_prox_pct", "rs_vs_market",
            "vcp_detected", "weekly_vol_ratio", "trend_template_pass",
            "initial_stop", "suggested_shares", "new_entry"]
    with pd.option_context("display.max_rows", None, "display.width", 250):
        print(show[[c for c in cols if c in show.columns]].to_string(index=False))


def print_dashboard(r):
    """Vertical single-ticker readout mirroring the TradingView panel order."""
    def fmt(v, suffix="", dash="---"):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return dash
        if isinstance(v, bool):
            return "DETECTED" if v else "none"
        return f"{v}{suffix}"

    rows = [
        ("TICKER", r["ticker"]),
        ("Price", f"${r['price']}"),
        ("TICKER STAGE", f"Stage {r['ticker_stage']}"),
        ("Stage 2 Duration", f"{r['stage2_weeks']} Weeks"),
        ("30WMA Trend", r["ma30_trend"]),
        ("Momentum", r["momentum"]),
        ("Current Ext %", fmt(r["current_ext_pct"], "%")),
        ("Peak S2 Ext", fmt(r["peak_s2_ext"], "%")),
        ("Avg Hist Peak", fmt(r["avg_hist_peak"], "%")),
        ("52W High Prox", fmt(r["high52_prox_pct"], "%")),
        ("RS vs Market", fmt(r["rs_vs_market"])),
        ("Pre-Launch VCP", fmt(r["vcp_detected"])),
        ("S2 Launch Net Vol", fmt(r["s2_launch_net_vol"], "x")),
        ("Weekly Vol Ratio", fmt(r["weekly_vol_ratio"], "x")),
        ("10d Net Vol Surge", fmt(r["net_vol_surge"], "x")),
        ("10d Peak Spikes", f"{fmt(r['peak_spike_up'],'x')} | {fmt(r['peak_spike_dn'],'x')}"),
        ("Half Exit (Mom)", f"${r['half_exit_mom']}"),
        ("Stage 2 Stop", f"${r['stage2_stop']}"),
        ("Full Exit (Chan)", f"${r['full_exit_chan']}"),
        ("Initial Stop Loss ($)", f"${r['initial_stop']}"),
        ("Initial Stop Loss (%)", f"{r['initial_stop_pct']}%"),
        ("Suggested Shares", r["suggested_shares"]),
        ("Position Value ($)", f"${r['position_value']}"),
        ("Trend Template", "PASS" if r["trend_template_pass"] else "FAIL"),
        ("NEW ENTRY", r["new_entry"]),
        ("ACTIVE TRADE", r["active_trade"]),
    ]
    width = max(len(k) for k, _ in rows)
    print("+" + "-" * (width + 26) + "+")
    for k, v in rows:
        print(f"| {k:<{width}}  {str(v):>22} |")
    print("+" + "-" * (width + 26) + "+")
    if not r["trend_template_pass"] and r["trend_template_failed"]:
        print(f"\nTrend Template failed on: {r['trend_template_failed']}")


if __name__ == "__main__":
    main()
