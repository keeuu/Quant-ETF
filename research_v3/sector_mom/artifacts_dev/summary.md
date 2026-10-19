# sector_mom development phase (score start to 2024-09-30)

Protocol sha256 d4e6eb305ec7b8cd878772b2ed58368b0d69a30a5c33be0f84a55bc70b91e887 (verified at run time). Holdout NOT loaded (guard: sessions after 2024-09-30 raise).

DEV: 2020-07-13 .. 2024-09-30; DEV_A: 2020-07-13 .. 2022-08-22; DEV_B: 2022-08-23 .. 2024-09-30.

## Selection outcome

**No strategy qualifies.** Holdout is not run unless the user explicitly asks for information only.

Qualifiers: []

Criteria (all required; primary cost 2.5+10 bp AND sensitivity 2.5+20 bp). Max drawdown is a positive depth, lower is better.

| strategy | cost | C1 DEV xSharpe > EW & CSI300 | C1 | C2 DEV MDD < CSI300 | C2 | C3 xSharpe > EW in A and B | C3 | cost-all | qualifies (both costs) |
|---|---|---|---|---|---|---|---|---|---|
| S1_WEEKLY_MOM | primary | -0.474 vs EW -0.269 / CSI -0.101 | False | 60.02% vs CSI 43.10% | False | A -0.477 vs -0.191; B -0.471 vs -0.351 | False | False |  |
| S1_WEEKLY_MOM | sens | -0.668 vs EW -0.279 / CSI -0.102 | False | 67.59% vs CSI 43.10% | False | A -0.656 vs -0.204; B -0.681 vs -0.358 | False | False | False |
| S2_MA10_EXIT_WEEKLY_REENTRY | primary | -0.959 vs EW -0.269 / CSI -0.101 | False | 68.52% vs CSI 43.10% | False | A -1.381 vs -0.191; B -0.511 vs -0.351 | False | False |  |
| S2_MA10_EXIT_WEEKLY_REENTRY | sens | -1.263 vs EW -0.279 / CSI -0.102 | False | 75.61% vs CSI 43.10% | False | A -1.671 vs -0.204; B -0.830 vs -0.358 | False | False | False |
| S3_MA5_EXIT_WEEKLY_REENTRY | primary | -1.232 vs EW -0.269 / CSI -0.101 | False | 70.11% vs CSI 43.10% | False | A -1.970 vs -0.191; B -0.530 vs -0.351 | False | False |  |
| S3_MA5_EXIT_WEEKLY_REENTRY | sens | -1.628 vs EW -0.279 / CSI -0.102 | False | 77.57% vs CSI 43.10% | False | A -2.379 vs -0.204; B -0.916 vs -0.358 | False | False | False |
| S4_MA10_EXIT_DAILY_REENTRY | primary | -1.173 vs EW -0.269 / CSI -0.101 | False | 75.26% vs CSI 43.10% | False | A -1.448 vs -0.191; B -0.885 vs -0.351 | False | False |  |
| S4_MA10_EXIT_DAILY_REENTRY | sens | -1.528 vs EW -0.279 / CSI -0.102 | False | 81.90% vs CSI 43.10% | False | A -1.785 vs -0.204; B -1.259 vs -0.358 | False | False | False |

## DEV main table (primary cost 2.5+10 bp, fractional shares, 1,000,000 CNY)

| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr | cost drag | round trips | fills | win rate | avg hold d | mean cash | lim-blk buy/sell |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1_WEEKLY_MOM | -12.55% | 26.81% | -0.474 | 60.02% | -27.28% (2022) | 24.84 | 5.66% | 486 | 2488 | 35.4% | 14.9 | 4.35% | 2/0 |
| S2_MA10_EXIT_WEEKLY_REENTRY | -17.53% | 21.03% | -0.959 | 68.52% | -30.02% (2022) | 30.24 | 6.59% | 625 | 2107 | 28.5% | 6.5 | 40.39% | 2/0 |
| S3_MA5_EXIT_WEEKLY_REENTRY | -18.60% | 17.95% | -1.232 | 70.11% | -33.77% (2022) | 33.84 | 7.29% | 709 | 1968 | 30.7% | 3.9 | 56.01% | 2/0 |
| S4_MA10_EXIT_DAILY_REENTRY | -22.00% | 21.93% | -1.173 | 75.26% | -31.36% (2022) | 36.57 | 7.64% | 757 | 2424 | 28.3% | 6.4 | 32.85% | 2/0 |
| CSI300_BH | -1.34% | 19.31% | -0.101 | 43.10% | -21.68% (2022) | 0.13 | 0.03% | 0 | 1 | n/a | nan | 0.00% | 0/0 |
| EW_SECTOR | -4.85% | 20.37% | -0.269 | 44.10% | -23.20% (2022) | 0.99 | 0.20% | 64 | 5134 | 18.8% | 248.2 | 0.07% | 0/0 |

MDD dates (DEV, primary): 
- S1_WEEKLY_MOM: peak 2020-07-13, trough 2024-09-02
- S2_MA10_EXIT_WEEKLY_REENTRY: peak 2020-07-13, trough 2024-09-04
- S3_MA5_EXIT_WEEKLY_REENTRY: peak 2020-07-13, trough 2024-09-12
- S4_MA10_EXIT_DAILY_REENTRY: peak 2020-07-13, trough 2024-09-04
- CSI300_BH: peak 2021-02-10, trough 2024-02-02
- EW_SECTOR: peak 2021-12-09, trough 2024-09-18

## xSharpe by period (primary / sensitivity)

| strategy | DEV | DEV_A | DEV_B |
|---|---|---|---|
| S1_WEEKLY_MOM | -0.474 / -0.668 | -0.477 / -0.656 | -0.471 / -0.681 |
| S2_MA10_EXIT_WEEKLY_REENTRY | -0.959 / -1.263 | -1.381 / -1.671 | -0.511 / -0.830 |
| S3_MA5_EXIT_WEEKLY_REENTRY | -1.232 / -1.628 | -1.970 / -2.379 | -0.530 / -0.916 |
| S4_MA10_EXIT_DAILY_REENTRY | -1.173 / -1.528 | -1.448 / -1.785 | -0.885 / -1.259 |
| CSI300_BH | -0.101 / -0.102 | -0.240 / -0.243 | 0.056 / 0.056 |
| EW_SECTOR | -0.269 / -0.279 | -0.191 / -0.204 | -0.351 / -0.358 |

## Sensitivity cost (2.5+20 bp) DEV table

| strategy | CAGR | vol | xSharpe | MDD | turnover/yr | cost drag | round trips | win rate | mean cash |
|---|---|---|---|---|---|---|---|---|---|
| S1_WEEKLY_MOM | -16.83% | 26.81% | -0.668 | 67.59% | 24.76 | 9.95% | 486 | 33.3% | 4.34% |
| S2_MA10_EXIT_WEEKLY_REENTRY | -22.45% | 21.01% | -1.263 | 75.61% | 30.17 | 11.51% | 625 | 26.4% | 40.37% |
| S3_MA5_EXIT_WEEKLY_REENTRY | -23.99% | 17.95% | -1.628 | 77.57% | 33.78 | 12.68% | 709 | 28.3% | 56.00% |
| S4_MA10_EXIT_DAILY_REENTRY | -27.62% | 21.91% | -1.528 | 81.90% | 36.42 | 13.26% | 757 | 26.2% | 32.83% |
| CSI300_BH | -1.37% | 19.30% | -0.102 | 43.10% | 0.13 | 0.05% | 0 | n/a | 0.00% |
| EW_SECTOR | -5.03% | 20.36% | -0.279 | 44.33% | 0.99 | 0.39% | 64 | 18.8% | 0.06% |

## Secondary: 100,000 CNY, 100-share lots, min 5 CNY commission (DEV)

| strategy | CAGR | vol | xSharpe | MDD | turnover/yr | round trips | mean cash | xSharpe (sens cost) |
|---|---|---|---|---|---|---|---|---|
| S1_WEEKLY_MOM | -14.27% | 26.77% | -0.552 | 63.15% | 24.58 | 486 | 4.47% | -0.758 |
| S2_MA10_EXIT_WEEKLY_REENTRY | -19.35% | 20.99% | -1.071 | 71.23% | 30.03 | 625 | 40.53% | -1.404 |
| S3_MA5_EXIT_WEEKLY_REENTRY | -20.31% | 17.90% | -1.358 | 72.60% | 33.64 | 709 | 56.15% | -1.795 |
| S4_MA10_EXIT_DAILY_REENTRY | -24.52% | 21.87% | -1.332 | 78.33% | 36.30 | 757 | 33.03% | -1.735 |
| CSI300_BH | -1.33% | 19.22% | -0.101 | 42.94% | 0.13 | 0 | 0.43% | -0.102 |
| EW_SECTOR | -6.79% | 20.16% | -0.380 | 47.36% | 0.83 | 64 | 0.63% | -0.388 |

## Calendar-year returns (primary, fractional; * = partial year)

| year | S1_WEEKLY_MOM | S2_MA10_EXIT_WEEKLY_REENTRY | S3_MA5_EXIT_WEEKLY_REENTRY | S4_MA10_EXIT_DAILY_REENTRY | CSI300_BH | EW_SECTOR |
|---|---|---|---|---|---|---|
| 2020* | -11.19% | -7.43% | -8.35% | -8.04% | 10.42% | 2.28% |
| 2021 | -4.26% | -27.79% | -25.18% | -30.61% | -2.40% | 7.12% |
| 2022 | -27.28% | -30.02% | -33.77% | -31.36% | -21.68% | -23.20% |
| 2023 | -3.27% | -7.47% | -9.36% | -19.47% | -10.43% | -9.54% |
| 2024* | -5.04% | 2.46% | 1.95% | -0.60% | 24.95% | 6.53% |

## Bootstrap 90% CI of excess-Sharpe difference (primary cost; stationary block 21, 2000 resamples, seed 20261019)

| period | strategy | vs | diff | lo | hi | CI includes 0 |
|---|---|---|---|---|---|---|
| DEV | S1_WEEKLY_MOM | EW_SECTOR | -0.205 | -0.771 | +0.374 | yes |
| DEV | S1_WEEKLY_MOM | CSI300_BH | -0.373 | -1.096 | +0.328 | yes |
| DEV | S2_MA10_EXIT_WEEKLY_REENTRY | EW_SECTOR | -0.690 | -1.361 | -0.127 | no |
| DEV | S2_MA10_EXIT_WEEKLY_REENTRY | CSI300_BH | -0.858 | -1.609 | -0.238 | no |
| DEV | S3_MA5_EXIT_WEEKLY_REENTRY | EW_SECTOR | -0.963 | -1.750 | -0.337 | no |
| DEV | S3_MA5_EXIT_WEEKLY_REENTRY | CSI300_BH | -1.132 | -1.909 | -0.496 | no |
| DEV | S4_MA10_EXIT_DAILY_REENTRY | EW_SECTOR | -0.905 | -1.542 | -0.372 | no |
| DEV | S4_MA10_EXIT_DAILY_REENTRY | CSI300_BH | -1.073 | -1.742 | -0.490 | no |
| DEV_A | S1_WEEKLY_MOM | EW_SECTOR | -0.286 | -1.126 | +0.478 | yes |
| DEV_A | S1_WEEKLY_MOM | CSI300_BH | -0.237 | -1.316 | +0.787 | yes |
| DEV_A | S2_MA10_EXIT_WEEKLY_REENTRY | EW_SECTOR | -1.190 | -2.045 | -0.406 | no |
| DEV_A | S2_MA10_EXIT_WEEKLY_REENTRY | CSI300_BH | -1.141 | -2.104 | -0.260 | no |
| DEV_A | S3_MA5_EXIT_WEEKLY_REENTRY | EW_SECTOR | -1.778 | -2.683 | -0.935 | no |
| DEV_A | S3_MA5_EXIT_WEEKLY_REENTRY | CSI300_BH | -1.730 | -2.620 | -0.864 | no |
| DEV_A | S4_MA10_EXIT_DAILY_REENTRY | EW_SECTOR | -1.257 | -2.118 | -0.452 | no |
| DEV_A | S4_MA10_EXIT_DAILY_REENTRY | CSI300_BH | -1.208 | -2.130 | -0.312 | no |
| DEV_B | S1_WEEKLY_MOM | EW_SECTOR | -0.120 | -0.996 | +0.754 | yes |
| DEV_B | S1_WEEKLY_MOM | CSI300_BH | -0.527 | -1.550 | +0.504 | yes |
| DEV_B | S2_MA10_EXIT_WEEKLY_REENTRY | EW_SECTOR | -0.160 | -1.189 | +0.663 | yes |
| DEV_B | S2_MA10_EXIT_WEEKLY_REENTRY | CSI300_BH | -0.567 | -1.683 | +0.341 | yes |
| DEV_B | S3_MA5_EXIT_WEEKLY_REENTRY | EW_SECTOR | -0.179 | -1.375 | +0.655 | yes |
| DEV_B | S3_MA5_EXIT_WEEKLY_REENTRY | CSI300_BH | -0.586 | -1.860 | +0.324 | yes |
| DEV_B | S4_MA10_EXIT_DAILY_REENTRY | EW_SECTOR | -0.534 | -1.501 | +0.206 | yes |
| DEV_B | S4_MA10_EXIT_DAILY_REENTRY | CSI300_BH | -0.941 | -1.934 | -0.129 | no |

## PSR (vs 0, excess returns) and DSR (N = 4), DEV

| cost | strategy | PSR(0) | DSR(N=4) | SR0 annualised |
|---|---|---|---|---|
| primary | S1_WEEKLY_MOM | 0.171 | 0.047 | 0.362 |
| primary | S2_MA10_EXIT_WEEKLY_REENTRY | 0.029 | 0.004 | 0.362 |
| primary | S3_MA5_EXIT_WEEKLY_REENTRY | 0.009 | 0.001 | 0.362 |
| primary | S4_MA10_EXIT_DAILY_REENTRY | 0.010 | 0.001 | 0.362 |
| primary | EW_SECTOR | 0.295 | n/a | 0.362 |
| primary | CSI300_BH | 0.420 | n/a | 0.362 |
| sens | S1_WEEKLY_MOM | 0.091 | 0.012 | 0.453 |
| sens | S2_MA10_EXIT_WEEKLY_REENTRY | 0.007 | 0.000 | 0.453 |
| sens | S3_MA5_EXIT_WEEKLY_REENTRY | 0.001 | 0.000 | 0.453 |
| sens | S4_MA10_EXIT_DAILY_REENTRY | 0.001 | 0.000 | 0.453 |
| sens | EW_SECTOR | 0.288 | n/a | 0.453 |
| sens | CSI300_BH | 0.419 | n/a | 0.453 |

## Universe

Included ETFs in classified list: 751; with price data: 398; without data: 32.
Eligible count (after dedup) over scored signal dates: min 16, median 59, max 81; pre-dedup min 18, max 121. First signal with >= 15: 2020-07-10 (executes 2020-07-13).


## Data

- Source: THS forward-adjusted bars from 2021-10-19 (primary) extended backwards with akshare/Sina self-built adjusted bars; {'THS+Sina_ext': 399}.
- Included codes without any price data: 32 (not found in THS or Sina); included ETFs with bars inside DEV: 398.
- Overlap check THS vs Sina (p50/p95/max): close-ratio deviation [0.0, 0.01728000558771403, 0.09263959390862953]; daily log-return difference [0.0, 0.014765306781488644, 0.0972155471638716]; turnover ratio [1.0, 1.0, 1.0000000298069611].
- Missing in-listing-span session bars: 762 across 49 ETFs (worst: [('512640', 263), ('159933', 95), ('512220', 78)]).
- Delisted ETFs: no source provides them; survivorship bias present (see deviations).

## Deviations and interpretations

Protocol rules (strategies, thresholds, periods, costs, selection rule) were implemented unchanged. Deviations (D) and
interpretations of ambiguous wording (I):

- **D1 Data source depth.** THS `/api/fund/market/historical` only serves the most recent 5 calendar years: a start date earlier than
  today-5y (2021-10-19 on the run date) returns an EMPTY result (not clipped), so the 5-year-window stitching in the brief is
  impossible. One window [2021-10-19, 2026-10-18] is fetched per ETF (THS, forward-adjusted, primary). History before the
  first THS bar comes from the protocol fallback (akshare Sina unadjusted bars + Sina hfq.js distributions, self-built total-return
  adjustment as in cn_transfer; Sina `amount` is the CNY turnover) and is scaled onto THS by the median close ratio over the
  overlap. Source per symbol is in `data/fetch_info.json`; overlap agreement (median daily log-return difference 0, 95th pct
  1.5%, max 9.7% for a few ETFs; turnover ratio 1.0) is in `data_report.json`. Consequence: the first ~1.3 of the 4.2 DEV years
  (2020-07-13 .. 2021-10-18) are priced from the Sina-built series.
- **D2 Tracked index source.** THS has no tracked-index field (fund profile has only the fund name, scale, managers). Tracked-index names come from
  akshare `fund_overview_em` (Eastmoney, 跟踪标的; benchmark text or fund name as fallback, recorded per ETF in `classification_basis`).
  The ETF universe is the THS code table (`fund-etf`, 1,716 SH/SZ) plus 34 ETF-named codes found only in akshare `fund_name_em`.
- **D3 Delisted ETFs.** None. The THS code table lists only currently listed ETFs (end_date/last_trade_date/list_date are null); akshare Sina/Eastmoney
  list none either. The universe therefore has SURVIVORSHIP BIAS (ETFs liquidated or merged before 2026-10 are absent). 32 of the 751 included
  codes (25 akshare-only, 7 THS) have no bars in either price source (THS code 3001/3004 or empty) and are unusable.
- **D4 Listing age.** THS `list_date` is null; the age of an ETF is the number of XSHG sessions from its first available bar (Sina starts at listing) to the
  signal date (>= 250 required).
- **D5 Limit rules on adjusted prices.** THS gives forward-adjusted bars only, so the limit test uses adjusted open / adjusted previous close
  (identical to the raw ratio except on ex-dividend days, a < 1% effect) with a 0.0005 tick tolerance. Limit is 20% for ETFs whose tracked
  index text contains 创业板 or 科创 (124 of 751) and 10% otherwise, as a proxy for "ETFs on 20% boards" (exchange rules actually
  give ETFs 10%).
- **D6 Costs.** 2.5 bp commission + 10 bp slippage (sensitivity 20 bp) are charged on traded notional at the open print (slippage is not a price shift).
  Lot variant: 100-share lots, commission = max(2.5 bp, 5 CNY) + slippage.
- **I1 Eligibility details.** Mean turnover is over the 60 sessions ending on the signal date (inclusive; missing bars count as 0). "Has a bar" means the
  signal date has a close. Missing in-history bars are forward-filled for return/SMA/sigma features only. Dedup key is the normalised tracked-index name; ties go to the
  lower ticker. 510300 is only a benchmark, never in the sector universe.
- **I2 S2-S4 exit state.** A held ETF whose close is below its SMA (10 or 5) at any close, including a weekly signal close, is sold at the next open and flagged
  "exited". An exited ETF is re-bought only at a weekly rebalance where it is selected AND close > SMA (S2, S3, S4) or, for S4 only, at the open after any close
  above the SMA while it is still in the latest weekly selection (at its latest weekly target weight). The flag clears when the ETF is re-bought or is no
  longer selected at a weekly signal. ETFs that were never exited are bought when selected without an extra MA condition (literal reading).
- **I3 Rebalance mechanics.** A weekly (EW: monthly) rebalance trades every held or targeted ETF to its target weight x portfolio value at the execution open
  (deltas, not only entries/exits). Sells are executed first and their proceeds are available for same-open buys; buys are scaled down if cash is
  short. Blocked orders (limit, no bar, T+1) are retried at each following open until filled or replaced by the next rebalance.
- **I4 Metrics.** Sharpe annualised with sqrt(252) on daily returns minus the daily SHIBOR 3M accrual (previous-session rate, calendar-day simple interest; none on the first
  session). CAGR uses calendar days / 365.25. Worst year uses full calendar years only (>= 200 sessions in the period; DEV has no full 2020 or 2024). Turnover is
  (buys + sells)/2 / mean NAV / years. Holding days are calendar days of closed round trips; win rate is net of costs; round trips still open at the end are excluded.
  DEV_A/DEV_B split at the calendar midpoint of DEV (2022-08-22). Scoring starts at the first weekly signal with >= 15 deduplicated eligible ETFs (2020-07-10, executes 2020-07-13).
  PSR is on daily excess returns vs 0; DSR uses N = 4 and the variance of the four strategies' daily Sharpe ratios. Bootstrap/PSR/DSR use primary costs.
- **I5 SHIBOR.** The cn_transfer snapshot (hash-verified) is copied into `data/`; rows after 2024-09-30 are stored but clipped by the loader.
- **I6 Holdout.** Bars after 2024-09-30 were downloaded and stored, but the loader clips them and raises `HoldoutAccessError` on any request beyond 2024-09-30; no holdout figure
  was computed. The weekly signal on 2024-09-30 (executes after the DEV end) is not traded.


