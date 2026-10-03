# Backtest: how often is an Active Buy positive?

3,717 signals from 2000-01-14 to 2026-09-25, 5,598 stocks. Same code as the Friday screen, using only data available on each Friday. Entry at the next Monday's open.

## Win rate = share of trades that ended positive

| Exit rule | Trades | Win rate | Average | Median |
|---|---|---|---|---|
| Book exit, investor (stop / Stage 3-4 / failed breakout) | 3,669 | **47.9%** | 3.4% | -0.3% |
| Book exit, trader (adds 30-week MA exit) | 3,687 | **46.9%** | 2.8% | -0.5% |
| Hold 13 weeks, no stop | 3,690 | **60.5%** | 3.0% | 2.1% |
| Hold 26 weeks, no stop | 3,640 | **62.9%** | 5.6% | 4.3% |
| Hold 52 weeks, no stop | 3,567 | **65.2%** | 9.9% | 7.7% |

## Compared with buying any stock on the same Fridays (baseline)

| Horizon | Active Buys positive | Any stock positive | Lift |
|---|---|---|---|
| 13 weeks | 60.5% | 54.8% (n=4,068,081) | +5.7 pts |
| 26 weeks | 62.9% | 56.3% (n=4,001,118) | +6.6 pts |
| 52 weeks | 65.2% | 58.2% (n=3,867,625) | +7.0 pts |

## By year of signal

| Year | Trades | Book-exit win | 26-wk win | Any stock 26-wk |
|---|---|---|---|---|
| 2000 | 23 | 56.5% | 69.6% | 58.3% |
| 2001 | 10 | 80.0% | 90.0% | 61.6% |
| 2002 | 56 | 48.2% | 55.4% | 47.2% |
| 2003 | 130 | 76.9% | 86.9% | 83.4% |
| 2004 | 130 | 40.8% | 56.9% | 62.7% |
| 2005 | 133 | 50.4% | 69.2% | 67.5% |
| 2006 | 198 | 62.1% | 68.7% | 66.9% |
| 2007 | 141 | 35.5% | 41.8% | 38.1% |
| 2009 | 29 | 41.4% | 65.5% | 77.5% |
| 2010 | 95 | 42.1% | 63.2% | 68.8% |
| 2011 | 76 | 28.9% | 35.5% | 50.4% |
| 2012 | 170 | 57.1% | 79.4% | 69.4% |
| 2013 | 281 | 59.8% | 73.3% | 70.5% |
| 2014 | 263 | 47.1% | 61.2% | 58.0% |
| 2015 | 170 | 39.4% | 46.5% | 42.6% |
| 2016 | 246 | 62.2% | 75.2% | 69.7% |
| 2017 | 321 | 56.1% | 69.2% | 61.7% |
| 2018 | 118 | 29.7% | 50.8% | 47.4% |
| 2019 | 227 | 44.1% | 55.1% | 46.0% |
| 2020 | 63 | 28.6% | 44.4% | 75.7% |
| 2021 | 203 | 34.5% | 46.3% | 42.9% |
| 2022 | 40 | 17.5% | 52.5% | 42.0% |
| 2023 | 112 | 47.3% | 75.0% | 53.0% |
| 2024 | 250 | 47.2% | 62.4% | 50.3% |
| 2025 | 125 | 28.6% | 59.2% | 55.7% |
| 2026 | 107 | 27.7% | 76.7% | 52.7% |

## By buy type

| Type | Trades | Book-exit win | 26-wk win |
|---|---|---|---|
| BREAKOUT - BUY | 23 | 43.5% | 73.9% |
| CONTINUATION - BUY | 2733 | 52.8% | 64.7% |
| PULLBACK - BUY | 961 | 34.1% | 57.5% |

Open positions (no sell signal yet): investor 48, trader 30; excluded from the book-exit rows.

## Read this before trusting the numbers

- Survivorship bias: only stocks listed today are in the data. Delisted and bankrupt companies are missing, so every row reads better than reality.
- Sector classification is today's; sector ETFs stand in for industry groups. 0 trades had no sector ETF history and skipped the sector gate.
- 696 of 3717 signals were, at some point after entry, flagged by the book's own sell logic as breakouts without enough volume (p.116: sell on the first rally). A buy that the sell side calls weak is a sign the buy and sell checks disagree; those trades exit at the first weekly close above the entry price (our reading of 'first rally'), at the next open.
- Our own choices, not the book's: entry at the next open, one position per stock, 'sell half' treated as sell all, 'take partial' ignored.
- No news, no contrary-opinion or price/dividend gauges, no commissions or slippage.
- A high win rate is not a high return: this counts positive trades, not how much they made.

## One account following the rules strictly vs SPY (2000-01-21 to 2026-10-02)

Start $100,000. Each position is 1/15 of the account at entry; signals taken in order, skipped when all 15 slots are full; cash earns nothing; investor book exit; no commissions, taxes or slippage.

Two SPY comparisons (SPY is total return, dividends included):
- **Matched SPY:** every time the strategy buys $X of a stock, the same $X goes into SPY that week; when the stock is sold, that SPY is sold the same week. Same dollars, same dates, same idle cash, so the only difference is stock picking and exits.
- **Buy and hold SPY:** fully invested from the first day.

| | Strategy | Matched SPY | Buy and hold SPY |
|---|---|---|---|
| Ending value | $543,000 (5.43x) | $883,000 (8.83x) | $852,000 (8.52x) |
| Per year (CAGR) | 6.5% | 8.5% | 8.4% |
| Worst drop (weekly closes) | -26.0% | -57.4% | -54.6% |
| Average share of account invested | 77.5% | 77.5% | 100% |
| Signals taken / skipped (slots full) | 837 / 2,880 | | |

| Year | Strategy | Matched SPY | Buy and hold SPY |
|---|---|---|---|
| 2000 | 38.7% | -5.4% | -8.2% |
| 2001 | 3.5% | -5.7% | -10.4% |
| 2002 | 10.3% | -37.8% | -23.5% |
| 2003 | 38.8% | 57.1% | 27.6% |
| 2004 | 15.2% | 22.6% | 12.3% |
| 2005 | 10.4% | 5.5% | 4.8% |
| 2006 | 7.5% | 28.0% | 15.8% |
| 2007 | -3.1% | 8.8% | 5.9% |
| 2008 | -7.0% | -9.8% | -39.4% |
| 2009 | 0.6% | 23.7% | 32.0% |
| 2010 | 16.6% | 20.7% | 14.0% |
| 2011 | -9.7% | -4.0% | 1.9% |
| 2012 | 10.8% | 14.1% | 14.1% |
| 2013 | 29.4% | 41.3% | 33.9% |
| 2014 | 16.8% | 16.5% | 15.6% |
| 2015 | -9.6% | -4.0% | 0.7% |
| 2016 | 22.0% | 6.6% | 11.0% |
| 2017 | 19.1% | 24.2% | 21.7% |
| 2018 | -5.6% | -2.7% | -5.4% |
| 2019 | 16.0% | 26.4% | 32.8% |
| 2020 | -7.9% | -9.2% | 16.4% |
| 2021 | 6.5% | 30.2% | 30.4% |
| 2022 | -11.9% | -19.9% | -18.2% |
| 2023 | -4.9% | 19.4% | 26.2% |
| 2024 | 3.1% | 21.0% | 26.8% |
| 2025 | -1.5% | 3.3% | 17.4% |
| 2026 | -5.9% | 4.8% | 12.4% |

Survivorship bias flatters the strategy side (delisted stocks are missing), so treat any edge over SPY as an upper bound.
