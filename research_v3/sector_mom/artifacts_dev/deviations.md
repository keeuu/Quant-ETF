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
