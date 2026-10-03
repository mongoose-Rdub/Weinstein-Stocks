# Variants vs SPY

Each row: strategy return per year minus buy-and-hold SPY (total return) over the same period. Positive = beat SPY. Development 2000-2012, validation 2013-2019, holdout 2020-2026.

| Variant | Dev | Valid | Holdout | All years | Worst drop (SPY) | Years beating SPY | Taken |
|---|---|---|---|---|---|---|---|
| BASE | +8.6 | -5.6 | -18.1 | -1.9 (6.5% vs 8.4%) | -21.2% (-54.6%) | 11/26 | 559 |
| RANK_TRIPLE | +8.5 | -6.0 | -16.4 | -1.5 (6.8% vs 8.4%) | -20.5% (-54.6%) | 9/26 | 517 |
| RANK_RS | +8.3 | -6.8 | -19.7 | -2.7 (5.6% vs 8.4%) | -30.8% (-54.6%) | 10/26 | 568 |
| RANK_VOL | +8.5 | -4.8 | -19.2 | -2.0 (6.3% vs 8.4%) | -26.4% (-54.6%) | 12/26 | 531 |
| NO_PULLBACKS | +4.5 | -5.9 | -12.0 | -2.3 (6.1% vs 8.4%) | -17.2% (-54.6%) | 10/26 | 520 |
| EARLY_ONLY | +9.1 | -6.2 | -17.1 | -1.5 (6.8% vs 8.4%) | -17.7% (-54.6%) | 10/26 | 557 |
| SECTOR_S2 | +3.7 | -7.6 | -15.9 | -4.2 (4.4% vs 8.5%) | -19.9% (-54.6%) | 7/26 | 491 |
| SLOTS_10 | +8.7 | -8.0 | -19.1 | -2.7 (5.6% vs 8.4%) | -26.0% (-54.6%) | 9/26 | 393 |
| SLOTS_20 | +8.0 | -6.0 | -15.6 | -1.6 (6.8% vs 8.4%) | -18.1% (-54.6%) | 10/26 | 719 |
| FULL_AT_S3 | +7.3 | -4.1 | -18.1 | -2.1 (6.3% vs 8.4%) | -22.8% (-54.6%) | 9/26 | 826 |
| IDLE_IN_SPY | +8.0 | -2.4 | -10.8 | +0.7 (9.0% vs 8.4%) | -54.5% (-54.6%) | 11/26 | 559 |
| BOOK_QUALITY | +3.6 | -3.2 | -12.2 | -2.1 (6.3% vs 8.4%) | -16.2% (-54.6%) | 10/26 | 514 |

## Did anything improve on the base?

- **RANK_TRIPLE**: does not pass (needs better in development and validation, and no worse in holdout) (dev -0.1, valid -0.4, holdout +1.7 pts per year vs base). Basis: p.150-152, p.157 (all three signals together).
- **RANK_RS**: does not pass (needs better in development and validation, and no worse in holdout) (dev -0.3, valid -1.2, holdout -1.6 pts per year vs base). Basis: p.110-113 (the stronger the RS the better).
- **RANK_VOL**: does not pass (needs better in development and validation, and no worse in holdout) (dev -0.1, valid +0.8, holdout -1.1 pts per year vs base). Basis: p.150 (volume is the confirmation), p.59.
- **NO_PULLBACKS**: does not pass (needs better in development and validation, and no worse in holdout) (dev -4.1, valid -0.3, holdout +6.1 pts per year vs base). Basis: p.150 (buy the breakout); pullbacks are the secondary buy, p.105/115.
- **EARLY_ONLY**: does not pass (needs better in development and validation, and no worse in holdout) (dev +0.5, valid -0.6, holdout +1.0 pts per year vs base). Basis: p.99, p.129 (early Stage 2 is the ideal; late is chasing).
- **SECTOR_S2**: does not pass (needs better in development and validation, and no worse in holdout) (dev -4.9, valid -2.0, holdout +2.2 pts per year vs base). Basis: p.80 (best: a group with the same pattern; healthy = not Stage 3 or 4).
- **SLOTS_10**: does not pass (needs better in development and validation, and no worse in holdout) (dev +0.1, valid -2.4, holdout -1.0 pts per year vs base). Basis: not from the book (sizing is ours).
- **SLOTS_20**: does not pass (needs better in development and validation, and no worse in holdout) (dev -0.6, valid -0.4, holdout +2.5 pts per year vs base). Basis: not from the book (sizing is ours).
- **FULL_AT_S3**: does not pass (needs better in development and validation, and no worse in holdout) (dev -1.3, valid +1.5, holdout +0.0 pts per year vs base). Basis: p.36 is for traders; investors sell half p.36-37.
- **IDLE_IN_SPY**: does not pass (needs better in development and validation, and no worse in holdout) (dev -0.6, valid +3.2, holdout +7.3 pts per year vs base). Basis: NOT from the book: a portfolio overlay.
- **BOOK_QUALITY**: does not pass (needs better in development and validation, and no worse in holdout) (dev -5.0, valid +2.4, holdout +5.9 pts per year vs base). Basis: combination of the three above.

## Variants tested

- **BASE**: As backtested: youngest Stage 2 first, 15 slots, investor sells half at Stage 3. Basis: p.36-37.
- **RANK_TRIPLE**: Take the best triple-confirmation score first (volume, RS, group). Basis: p.150-152, p.157 (all three signals together).
- **RANK_RS**: Take the strongest relative strength first. Basis: p.110-113 (the stronger the RS the better).
- **RANK_VOL**: Take the heaviest breakout volume first. Basis: p.150 (volume is the confirmation), p.59.
- **NO_PULLBACKS**: Skip pullback buys (the weakest type in the first backtest). Basis: p.150 (buy the breakout); pullbacks are the secondary buy, p.105/115.
- **EARLY_ONLY**: Only Stage 2 runs 26 weeks old or less. Basis: p.99, p.129 (early Stage 2 is the ideal; late is chasing).
- **SECTOR_S2**: Only when the sector itself is in Stage 2 (not Stage 1). Basis: p.80 (best: a group with the same pattern; healthy = not Stage 3 or 4).
- **SLOTS_10**: 10 positions instead of 15. Basis: not from the book (sizing is ours).
- **SLOTS_20**: 20 positions instead of 15. Basis: not from the book (sizing is ours).
- **FULL_AT_S3**: Investor sells all at Stage 3 (the earlier backtest). Basis: p.36 is for traders; investors sell half p.36-37.
- **IDLE_IN_SPY**: Idle cash held in SPY. Basis: NOT from the book: a portfolio overlay.
- **BOOK_QUALITY**: RANK_TRIPLE + no pullbacks + early only (declared in advance, not picked from results). Basis: combination of the three above.

Read with care: eleven variants were tried, so one will look good by luck; only a variant that passes development and validation AND holdout, and has a basis in the book, is worth a conversation. Survivorship bias still flatters every row, and there are no costs or taxes.
