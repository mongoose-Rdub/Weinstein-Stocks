"""Universe building and price fetching for the full-US-market screen.

Weinstein worked from chart books covering thousands of NYSE, Amex and
over-the-counter stocks (p.31), not a 1,500-name index. This module builds that
wider universe and fetches its prices without tripping Yahoo's rate limiter:

  * Weekly refresh is INCREMENTAL. A cached stock only needs the last few
    weeks downloaded, which is a small fraction of a 4-year download.
  * Downloads adapt to throttling: the batch size shrinks and the pause grows
    exponentially (with jitter) when Yahoo starts returning empty frames, then
    both recover once it stops.
  * Tickers Yahoo keeps refusing are retried against Stooq.
  * A time budget ends fetching cleanly; the screen then runs on whatever was
    fetched, and the coverage is reported.
"""
import json
import os
import random
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

import stage_vcp_screener as infra

NASDAQ_API = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

# Nasdaq's sector names onto the 11-sector panel. Used only when neither the
# S&P GICS table nor Yahoo's own classification is available for a stock.
NASDAQ_SECTOR_MAP = {
    "Technology": "Tech", "Health Care": "Health", "Finance": "Finance",
    "Consumer Discretionary": "Cons Disc", "Consumer Staples": "Staples",
    "Industrials": "Industrial", "Energy": "Energy", "Basic Materials": "Materials",
    "Real Estate": "Real Estate", "Utilities": "Utility",
    "Telecommunications": "Telecom", "Communication Services": "Telecom",
}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def norm_symbol(s):
    return str(s).strip().upper().replace("/", "-").replace(".", "-")


# ---------------------------------------------------------------- universe
def nasdaq_rows(timeout=90, retries=3, sleep=time.sleep):
    """Every listed stock with last price, volume and sector, in one request."""
    try:
        import requests
    except ImportError:
        return None
    for attempt in range(retries):
        try:
            r = requests.get(NASDAQ_API, headers=BROWSER_HEADERS, timeout=timeout)
            r.raise_for_status()
            rows = r.json()["data"]["rows"]
            if rows:
                df = pd.DataFrame(rows)
                df["sym"] = df["symbol"].map(norm_symbol)
                df["price"] = pd.to_numeric(df["lastsale"].astype(str).str.replace(r"[$,]", "", regex=True),
                                            errors="coerce")
                df["vol"] = pd.to_numeric(df["volume"].astype(str).str.replace(",", ""), errors="coerce")
                df["mcap"] = pd.to_numeric(df["marketCap"].astype(str).str.replace(",", ""), errors="coerce")
                return df.drop_duplicates("sym").set_index("sym")
        except Exception as exc:
            log(f"  nasdaq screener attempt {attempt + 1} failed: {exc}")
            sleep(5 * (attempt + 1))
    return None


def load_sector_cache(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save_sector_cache(path, cache):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(cache, f, separators=(",", ":"), sort_keys=True)
    except Exception as exc:
        log(f"  could not save sector cache: {exc}")


def yf_sector_fill(tickers, cache, budget, deadline, lookup=None, sleep=time.sleep):
    """Ask Yahoo for each stock's own sector, a few hundred per run, biggest
    companies first. Stops quickly when Yahoo starts refusing; what it learned
    is cached, so the table improves week over week."""
    if lookup is None:
        import yfinance as yf

        def lookup(t):
            info = yf.Ticker(t).get_info()
            return info.get("sector")
    done = misses = 0
    for t in tickers:
        if done >= budget or time.time() > deadline or misses >= 25:
            break
        try:
            sec = lookup(t)
        except Exception:
            sec = None
        if sec and infra.YF_SECTOR_MAP.get(sec):
            cache[t] = {"s": infra.YF_SECTOR_MAP[sec], "src": "yf"}
            done += 1
            misses = 0
        else:
            misses += 1
            sleep(random.uniform(0.5, 1.5))
        sleep(0.15)
    return done


_SPAC = None


def is_blank_check(row):
    """Blank-check shells (SPACs) are cash boxes trading flat near $10, not
    operating companies; their flat 'bases' are noise to a stage screen."""
    import re
    ind = str(row.get("industry", "") or "")
    if ind == "Blank Checks":
        return True
    name = str(row.get("name", "") or "")
    p = row.get("price")
    return bool(re.search(r"acquisition (corp|company|co\b|holdings|limited|ltd)", name, re.I)
                and pd.notna(p) and 9.0 <= p <= 12.5)


def build_universe(min_price=0.0, prefilter_dollar_vol=0, sector_path="data/sectors.json",
                   sector_budget=600, deadline=None, rows=None, listed=None, sp_map=None,
                   lookup=None, sleep=time.sleep):
    """-> (tickers, sector_map, meta)."""
    deadline = deadline or (time.time() + 3600)
    listed = listed if listed is not None else infra.load_all_us_listed()
    if sp_map is None:
        try:
            _, sp_map = infra.load_sp1500()
        except Exception as exc:
            log(f"  S&P constituent lists unavailable: {exc}")
            sp_map = {}
    if rows is None:
        rows = nasdaq_rows(sleep=sleep)

    meta = {"listed": len(listed), "api": rows is not None}
    tickers = [norm_symbol(t) for t in listed]
    if rows is not None:
        keep = []
        for t in tickers:
            if t in sp_map:                       # index members always stay
                keep.append(t)
                continue
            if t not in rows.index:
                continue
            p, v = rows.at[t, "price"], rows.at[t, "vol"]
            if is_blank_check(rows.loc[t]):
                continue
            if min_price and pd.notna(p) and p < min_price:
                continue
            if prefilter_dollar_vol and pd.notna(p) and pd.notna(v) and p * v < prefilter_dollar_vol:
                continue
            keep.append(t)
        tickers = keep
    tickers = sorted(set(tickers) | set(sp_map))
    meta["after_prefilter"] = len(tickers)

    cache = load_sector_cache(sector_path)
    # Yahoo's sector for the largest names that no S&P table covers
    if rows is not None:
        need = [t for t in tickers if t not in sp_map and t not in cache and t in rows.index]
        need.sort(key=lambda t: -(rows.at[t, "mcap"] if pd.notna(rows.at[t, "mcap"]) else 0))
    else:
        need = [t for t in tickers if t not in sp_map and t not in cache]
    if sector_budget and need:
        n = yf_sector_fill(need, cache, sector_budget, deadline, lookup=lookup, sleep=sleep)
        log(f"  Yahoo sector lookups this run: {n}")
        save_sector_cache(sector_path, cache)

    smap, src = {}, Counter()
    for t in tickers:
        if t in sp_map:
            smap[t] = sp_map[t]; src["sp"] += 1
        elif t in cache and cache[t].get("s"):
            smap[t] = cache[t]["s"]; src["yahoo"] += 1
        elif rows is not None and t in rows.index and NASDAQ_SECTOR_MAP.get(str(rows.at[t, "sector"])):
            smap[t] = NASDAQ_SECTOR_MAP[str(rows.at[t, "sector"])]; src["nasdaq"] += 1
    meta["sector_sources"] = dict(src)
    meta["no_sector"] = len(tickers) - len(smap)
    return tickers, smap, meta


# ------------------------------------------------------------------ prices
def last_friday(today=None):
    today = today or date.today()
    return today - timedelta(days=(today.weekday() - 4) % 7)


def merge_incremental(old, new, tol=0.03):
    """Append a short fresh download to a cached history.

    Prices are dividend-adjusted, so a new dividend rescales the whole history.
    The overlap tells us by how much; a larger break (a split) returns None so
    the caller refetches the full history."""
    if old is None or new is None or old.empty or new.empty:
        return None
    ov = old.index.intersection(new.index)
    if len(ov) < 3:
        return None
    r = float(np.median(new.loc[ov, "Close"].to_numpy() / old.loc[ov, "Close"].to_numpy()))
    if not np.isfinite(r) or abs(r - 1) > tol:
        return None
    o = old[old.index < new.index[0]].copy()
    for c in ("Open", "High", "Low", "Close"):
        o[c] = o[c] * r
    out = pd.concat([o, new]).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out.tail(1100)


class Throttle:
    """Batch size and pause that react to how Yahoo is answering."""

    def __init__(self, chunk=60, pause=1.5, lo=10, hi=120):
        self.chunk, self.pause, self.lo, self.hi = chunk, pause, lo, hi
        self.backoff = 0.0
        self.good = 0
        self.throttled_in_a_row = 0

    def ok(self):
        self.good += 1
        self.throttled_in_a_row = 0
        self.backoff = 0.0
        if self.good >= 2:
            self.chunk = min(self.hi, int(self.chunk * 1.25) + 1)
            self.pause = max(1.0, self.pause * 0.9)

    def hit(self):
        self.good = 0
        self.throttled_in_a_row += 1
        self.chunk = max(self.lo, self.chunk // 2)
        self.backoff = min(900.0, max(60.0, self.backoff * 2))
        self.pause = min(10.0, self.pause * 1.5)
        return self.backoff * random.uniform(0.8, 1.2)


def ensure_prices(tickers, today=None, time_budget_min=240, dl_yahoo=None, dl_stooq=None,
                  load=None, save=None, sleep=time.sleep, now=time.time):
    """Bring every ticker's cached daily history up to the last completed Friday.

    Returns a stats dict. dl_* take (tickers, period) and return
    {ticker: (weekly, daily)}; they are injectable for testing."""
    dl_yahoo = dl_yahoo or (lambda b, p: infra._download_batch_yahoo(b, p, True))
    dl_stooq = dl_stooq or (lambda b, p: infra._download_batch_stooq(b, p, True))
    load = load or (lambda t: _load_any_age(t))
    save = save or infra.save_cached
    target = last_friday(today)
    deadline = now() + time_budget_min * 60

    current, incr, full = [], [], []
    for t in tickers:
        df = load(t)
        if df is None or df.empty:
            full.append(t)
        elif df.index[-1].date() >= target:
            current.append(t)
        elif (target - df.index[-1].date()).days <= 120 and len(df) > 300:
            incr.append(t)
        else:
            full.append(t)
    stats = {"total": len(tickers), "current": len(current), "incremental": len(incr),
             "full": len(full), "fetched": 0, "stooq": 0, "failed": 0, "throttle_hits": 0,
             "ended_early": False}
    log(f"Prices: {len(current)} current, {len(incr)} to refresh, {len(full)} to download in full")

    th = Throttle()
    attempts = Counter()
    gave_up = []

    def run(queue, period, merge):
        nonlocal gave_up
        queue = list(queue)
        while queue:
            if now() > deadline:
                stats["ended_early"] = True
                gave_up += queue
                return
            batch, queue = queue[:th.chunk], queue[th.chunk:]
            res = dl_yahoo(batch, period)
            got = 0
            for t in batch:
                if t not in res:
                    continue
                daily = res[t][1]
                if merge:
                    merged = merge_incremental(load(t), daily)
                    if merged is None:           # split or gap: take the full history
                        full_q.append(t)
                        continue
                    daily = merged
                save(t, daily)
                got += 1
            stats["fetched"] += got
            missed = [t for t in batch if t not in res]
            ratio = len(res) / max(len(batch), 1)
            # a tiny batch of misses is more likely delisted tickers than throttling
            throttled = len(batch) >= 10 and ratio < 0.5
            if ratio >= 0.85:
                th.ok()
            else:
                wait = th.hit() if throttled else 0.0
                stats["throttle_hits"] += 1 if throttled else 0
                if wait:
                    log(f"  Yahoo is throttling ({len(res)}/{len(batch)}); batch {th.chunk}, waiting {wait:.0f}s")
                    sleep(wait)
            for t in missed:
                if throttled:                    # Yahoo's fault, not the ticker's
                    queue.append(t)
                    continue
                attempts[t] += 1
                (queue if attempts[t] < 3 else gave_up).append(t)
            if th.throttled_in_a_row >= 4:       # Yahoo is not coming back soon
                log("  Yahoo keeps refusing: switching the rest to Stooq")
                gave_up += queue
                queue = []
            sleep(th.pause)

    full_q = list(full)
    run(incr, "3mo", True)
    run(full_q, "4y", False)

    # the stragglers: Stooq is far more tolerant of sustained requests
    left = [t for t in dict.fromkeys(gave_up) if not (load(t) is not None and
                                                      load(t).index[-1].date() >= target)]
    if left and not stats["ended_early"]:
        log(f"Trying Stooq for {len(left)} tickers Yahoo would not serve")
        for i in range(0, len(left), 100):
            if now() > deadline:
                stats["ended_early"] = True
                break
            res = dl_stooq(left[i:i + 100], "4y")
            for t, (_, daily) in res.items():
                save(t, daily)
                stats["stooq"] += 1
            sleep(1.0)
    ok = 0
    for t in tickers:
        d = load(t)
        if d is not None and not d.empty and (target - d.index[-1].date()).days <= 7:
            ok += 1
    stats["failed"] = len(tickers) - ok
    stats["usable"] = ok
    return stats


def _load_any_age(ticker):
    try:
        return pd.read_pickle(infra._cache_file(ticker))
    except Exception:
        return None


def load_any(ticker):
    return _load_any_age(ticker)
