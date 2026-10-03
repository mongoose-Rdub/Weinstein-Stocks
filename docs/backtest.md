# Backtest: how often is an Active Buy positive?

3,561 signals from 2000-01-14 to 2026-09-25, 5,598 stocks. Same code as the Friday screen, using only data available on each Friday. Entry at the next Monday's open.

## Win rate = share of trades that ended positive

| Exit rule | Trades | Win rate | Average | Median |
|---|---|---|---|---|
| Book exit, investor (sell half at Stage 3, rest on stop / Stage 4) | 3,482 | **48.8%** | 4.1% | -0.2% |
| Book exit, trader (adds 30-week MA exit) | 3,535 | **46.8%** | 2.6% | -0.5% |
| Hold 13 weeks, no stop | 3,538 | **60.4%** | 3.0% | 2.0% |
| Hold 26 weeks, no stop | 3,489 | **62.7%** | 5.6% | 4.1% |
| Hold 52 weeks, no stop | 3,417 | **65.0%** | 9.9% | 7.6% |

## Compared with buying any stock on the same Fridays (baseline)

| Horizon | Active Buys positive | Any stock positive | Lift |
|---|---|---|---|
| 13 weeks | 60.4% | 54.8% (n=4,068,081) | +5.6 pts |
| 26 weeks | 62.7% | 56.3% (n=4,001,118) | +6.4 pts |
| 52 weeks | 65.0% | 58.2% (n=3,867,625) | +6.8 pts |

## By year of signal

| Year | Trades | Book-exit win | 26-wk win | Any stock 26-wk |
|---|---|---|---|---|
| 2000 | 23 | 60.9% | 69.6% | 58.3% |
| 2001 | 10 | 80.0% | 90.0% | 61.6% |
| 2002 | 53 | 47.2% | 52.8% | 47.2% |
| 2003 | 118 | 78.0% | 86.4% | 83.4% |
| 2004 | 122 | 48.4% | 56.6% | 62.7% |
| 2005 | 123 | 56.9% | 70.7% | 67.5% |
| 2006 | 190 | 61.6% | 68.4% | 66.9% |
| 2007 | 139 | 33.8% | 42.4% | 38.1% |
| 2009 | 29 | 41.4% | 65.5% | 77.5% |
| 2010 | 93 | 41.9% | 62.4% | 68.8% |
| 2011 | 76 | 32.9% | 35.5% | 50.4% |
| 2012 | 165 | 62.4% | 78.8% | 69.4% |
| 2013 | 275 | 63.6% | 73.5% | 70.5% |
| 2014 | 234 | 47.4% | 59.4% | 58.0% |
| 2015 | 157 | 38.2% | 47.1% | 42.6% |
| 2016 | 231 | 62.8% | 74.0% | 69.7% |
| 2017 | 308 | 60.4% | 69.2% | 61.7% |
| 2018 | 113 | 28.3% | 49.6% | 47.4% |
| 2019 | 221 | 37.6% | 54.8% | 46.0% |
| 2020 | 62 | 27.9% | 45.2% | 75.7% |
| 2021 | 201 | 33.3% | 46.8% | 42.9% |
| 2022 | 38 | 18.4% | 52.6% | 42.0% |
| 2023 | 109 | 51.4% | 76.1% | 53.0% |
| 2024 | 250 | 45.4% | 62.4% | 50.3% |
| 2025 | 119 | 24.5% | 60.5% | 55.7% |
| 2026 | 102 | 30.6% | 76.7% | 52.7% |

## By buy type

| Type | Trades | Book-exit win | 26-wk win |
|---|---|---|---|
| BREAKOUT - BUY | 21 | 47.6% | 76.2% |
| CONTINUATION - BUY | 2603 | 53.9% | 64.5% |
| PULLBACK - BUY | 937 | 34.6% | 57.3% |

Open positions (no sell signal yet): investor 79, trader 26; excluded from the book-exit rows.

## Read this before trusting the numbers

- Survivorship bias: only stocks listed today are in the data. Delisted and bankrupt companies are missing, so every row reads better than reality.
- Sector classification is today's; sector ETFs stand in for industry groups. 0 trades had no sector ETF history and skipped the sector gate.
- 661 of 3561 signals were, at some point after entry, flagged by the book's own sell logic as breakouts without enough volume (p.116: sell on the first rally). A buy that the sell side calls weak is a sign the buy and sell checks disagree; those trades exit at the first weekly close above the entry price (our reading of 'first rally'), at the next open.
- Our own choices, not the book's: entry at the next open, one position per stock, 'take partial' signals ignored. The investor sells half at Stage 3 and keeps half (p.36-37); the trader sells all (p.36).
- No news, no contrary-opinion or price/dividend gauges, no commissions or slippage.
- A high win rate is not a high return: this counts positive trades, not how much they made.

## One account following the rules strictly vs SPY (2000-01-21 to 2026-10-02)

Start $100,000. Each position is 1/15 of the account at entry; signals taken in order, skipped when all 15 slots are full; cash earns nothing; investor book exit; no commissions, taxes or slippage.

Two SPY comparisons (SPY is total return, dividends included):
- **Matched SPY:** every time the strategy buys $X of a stock, the same $X goes into SPY that week; when the stock is sold, that SPY is sold the same week. Same dollars, same dates, same idle cash, so the only difference is stock picking and exits.
- **Buy and hold SPY:** fully invested from the first day.

| | Strategy | Matched SPY | Buy and hold SPY |
|---|---|---|---|
| Ending value | $534,000 (5.34x) | $860,000 (8.6x) | $852,000 (8.52x) |
| Per year (CAGR) | 6.5% | 8.4% | 8.4% |
| Worst drop (weekly closes) | -21.2% | -53.2% | -54.6% |
| Average share of account invested | 73.6% | 73.6% | 100% |
| Signals taken / skipped (slots full) | 559 / 3,002 | | |

| Year | Strategy | Matched SPY | Buy and hold SPY |
|---|---|---|---|
| 2000 | 38.4% | -5.7% | -8.2% |
| 2001 | 3.0% | -7.5% | -10.4% |
| 2002 | 13.0% | -34.9% | -23.5% |
| 2003 | 33.6% | 46.4% | 27.6% |
| 2004 | 14.1% | 21.1% | 12.3% |
| 2005 | 14.7% | 6.6% | 4.8% |
| 2006 | 16.7% | 30.8% | 15.8% |
| 2007 | 2.1% | 8.5% | 5.9% |
| 2008 | -9.0% | -14.0% | -39.4% |
| 2009 | -2.4% | 29.0% | 32.0% |
| 2010 | 16.2% | 20.6% | 14.0% |
| 2011 | -7.0% | -1.6% | 1.9% |
| 2012 | 8.9% | 15.9% | 14.1% |
| 2013 | 18.4% | 36.5% | 33.9% |
| 2014 | 8.1% | 15.7% | 15.6% |
| 2015 | -2.0% | -0.5% | 0.7% |
| 2016 | 13.3% | 6.3% | 11.0% |
| 2017 | 20.4% | 18.7% | 21.7% |
| 2018 | -4.4% | -2.1% | -5.4% |
| 2019 | 12.5% | 27.1% | 32.8% |
| 2020 | -9.1% | -10.4% | 16.4% |
| 2021 | 3.3% | 27.1% | 30.4% |
| 2022 | -4.5% | -17.8% | -18.2% |
| 2023 | -2.4% | 20.5% | 26.2% |
| 2024 | 4.5% | 21.3% | 26.8% |
| 2025 | -6.7% | 3.2% | 17.4% |
| 2026 | -2.7% | 6.4% | 12.4% |

Survivorship bias flatters the strategy side (delisted stocks are missing), so treat any edge over SPY as an upper bound.
