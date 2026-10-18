# CN transfer test of frozen CAP_DEF_M (protocol_cn_transfer.json)

Outcome (primary cost 5+5 bps, point estimates): **SUPPORTED**

## Status under the ORIGINAL protocol (fail-closed on missing bars)

- CAP_DEF_M: NOT RUN: EngineError: Session 2015-04-13: missing or invalid close for 510500: None
- CSI300_BH: RUN
- CN_60_40: RUN
- BASE_M_CN: NOT RUN: EngineError: Session 2015-04-13: missing or invalid close for 510500: None

The strategy runs that hold the ETFs with missing bars are NOT RUN under the original protocol. All results below are from protocol_cn_transfer_amendment1.json (suspension rules), applied identically to all four strategies and both cost settings.

## Criteria (amended run)

| criterion | met |
|---|---|
| FULL excess Sharpe CAP_DEF_M > CN_60_40 | True |
| FULL max drawdown CAP_DEF_M < CSI300_BH | True |
| SUB_A excess Sharpe CAP_DEF_M > CN_60_40 | True |
| SUB_B excess Sharpe CAP_DEF_M > CN_60_40 | True |

Outcome rule: SUPPORTED = all met; NOT_SUPPORTED = neither the drawdown criterion nor the FULL Sharpe criterion met; otherwise PARTIAL.

Sample: data_start 2013-07-29, score_start 2014-09-01 (first monthly execution with all 6 assets warmed up), score_end 2026-09-30. SHIBOR cash, akshare 1.19.1, downloaded 2026-10-18.

## Metrics: Primary (5+5 bps)

### FULL (2014-09-01 to 2026-09-30)

| strategy | CAGR | vol | Sharpe | xSharpe | MDD | worst yr | turnover/yr | cash wt | bond-def wt | PSR(0) |
|---|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 10.22% | 13.91% | 0.795 | 0.590 | 33.84% | -0.93% | 2.47 | 7.86% | 11.50% | 0.976 |
| CSI300_BH | 7.02% | 22.62% | 0.424 | 0.299 | 45.44% | -24.16% | 0.08 | 0.00% | 0.00% | 0.845 |
| CN_60_40 | 6.15% | 13.42% | 0.528 | 0.316 | 28.50% | -12.75% | 0.22 | 0.00% | 0.00% | 0.859 |
| BASE_M_CN | 8.69% | 18.88% | 0.553 | 0.402 | 47.35% | -8.37% | 2.42 | 0.00% | 0.00% | 0.912 |

### SUB_A (2014-09-01 to 2020-12-31)

| strategy | CAGR | vol | Sharpe | xSharpe | MDD | worst yr | turnover/yr | cash wt | bond-def wt | PSR(0) |
|---|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 11.82% | 15.02% | 0.845 | 0.612 | 33.84% | -0.93% | 2.57 | 8.50% | 12.05% | 0.931 |
| CSI300_BH | 14.97% | 25.96% | 0.686 | 0.551 | 45.44% | -24.16% | 0.16 | 0.00% | 0.00% | 0.911 |
| CN_60_40 | 10.91% | 15.37% | 0.774 | 0.546 | 28.50% | -12.75% | 0.30 | 0.00% | 0.00% | 0.909 |
| BASE_M_CN | 10.87% | 21.63% | 0.603 | 0.441 | 47.35% | -8.37% | 2.52 | 0.00% | 0.00% | 0.858 |

### SUB_B (2021-01-04 to 2026-09-30)

| strategy | CAGR | vol | Sharpe | xSharpe | MDD | worst yr | turnover/yr | cash wt | bond-def wt | PSR(0) |
|---|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 8.49% | 12.58% | 0.735 | 0.567 | 13.50% | -0.30% | 2.36 | 7.16% | 10.89% | 0.905 |
| CSI300_BH | -1.10% | 18.22% | 0.028 | -0.088 | 42.18% | -20.13% | 0.00 | 0.00% | 0.00% | 0.418 |
| CN_60_40 | 1.14% | 10.84% | 0.163 | -0.032 | 24.34% | -11.31% | 0.12 | 0.00% | 0.00% | 0.470 |
| BASE_M_CN | 6.35% | 15.27% | 0.496 | 0.357 | 18.93% | -2.99% | 2.32 | 0.00% | 0.00% | 0.797 |

## Metrics: Secondary sensitivity (10+10 bps), not used for decisions

### FULL (2014-09-01 to 2026-09-30)

| strategy | CAGR | vol | Sharpe | xSharpe | MDD | worst yr | turnover/yr | cash wt | bond-def wt | PSR(0) |
|---|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 9.79% | 13.91% | 0.766 | 0.561 | 33.93% | -1.46% | 2.47 | 7.86% | 11.50% | 0.970 |
| CSI300_BH | 7.01% | 22.62% | 0.424 | 0.298 | 45.44% | -24.16% | 0.08 | 0.00% | 0.00% | 0.845 |
| CN_60_40 | 6.11% | 13.42% | 0.526 | 0.314 | 28.52% | -12.77% | 0.22 | 0.00% | 0.00% | 0.857 |
| BASE_M_CN | 8.18% | 18.88% | 0.527 | 0.376 | 47.35% | -8.79% | 2.42 | 0.00% | 0.00% | 0.897 |

### SUB_A (2014-09-01 to 2020-12-31)

| strategy | CAGR | vol | Sharpe | xSharpe | MDD | worst yr | turnover/yr | cash wt | bond-def wt | PSR(0) |
|---|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 11.38% | 15.02% | 0.817 | 0.584 | 33.93% | -1.46% | 2.57 | 8.50% | 12.05% | 0.922 |
| CSI300_BH | 14.95% | 25.96% | 0.686 | 0.550 | 45.44% | -24.16% | 0.16 | 0.00% | 0.00% | 0.911 |
| CN_60_40 | 10.86% | 15.37% | 0.771 | 0.543 | 28.52% | -12.77% | 0.30 | 0.00% | 0.00% | 0.908 |
| BASE_M_CN | 10.33% | 21.62% | 0.579 | 0.417 | 47.35% | -8.79% | 2.52 | 0.00% | 0.00% | 0.845 |

### SUB_B (2021-01-04 to 2026-09-30)

| strategy | CAGR | vol | Sharpe | xSharpe | MDD | worst yr | turnover/yr | cash wt | bond-def wt | PSR(0) |
|---|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 8.07% | 12.58% | 0.704 | 0.536 | 13.56% | -0.95% | 2.36 | 7.16% | 10.89% | 0.892 |
| CSI300_BH | -1.10% | 18.22% | 0.028 | -0.088 | 42.18% | -20.13% | 0.00 | 0.00% | 0.00% | 0.418 |
| CN_60_40 | 1.12% | 10.84% | 0.160 | -0.034 | 24.39% | -11.34% | 0.12 | 0.00% | 0.00% | 0.468 |
| BASE_M_CN | 5.86% | 15.27% | 0.464 | 0.326 | 19.46% | -3.61% | 2.32 | 0.00% | 0.00% | 0.775 |

## Calendar-year returns (primary cost; * = partial year)

| year | CAP_DEF_M | CSI300_BH | CN_60_40 | BASE_M_CN |
|---|---|---|---|---|
| 2014* | 23.38% | 49.83% | 30.11% | 18.88% |
| 2015 | 1.34% | 6.80% | 8.37% | -8.37% |
| 2016 | 4.11% | -9.69% | -4.75% | 6.47% |
| 2017 | 6.45% | 23.37% | 12.95% | 16.28% |
| 2018 | -0.93% | -24.16% | -12.75% | -0.77% |
| 2019 | 16.11% | 38.59% | 23.50% | 12.44% |
| 2020 | 27.31% | 29.08% | 17.89% | 27.77% |
| 2021 | -0.22% | -4.04% | -0.62% | -0.92% |
| 2022 | -0.30% | -20.13% | -11.31% | -2.99% |
| 2023 | -0.23% | -9.78% | -4.55% | 0.90% |
| 2024 | 14.83% | 17.37% | 14.65% | 10.42% |
| 2025 | 41.05% | 20.84% | 12.57% | 37.46% |
| 2026* | -0.74% | -4.33% | -1.70% | -3.26% |

## Bootstrap: 90% CI of excess-Sharpe difference (CAP_DEF_M minus reference), primary cost

Stationary block bootstrap, mean block 21, 2000 resamples, seed 20261019.

| partition | reference | diff | CI lo | CI hi | CI includes 0 |
|---|---|---|---|---|---|
| FULL | CN_60_40 | +0.274 | -0.065 | +0.678 | True |
| FULL | CSI300_BH | +0.292 | -0.045 | +0.692 | True |
| SUB_A | CN_60_40 | +0.066 | -0.356 | +0.627 | True |
| SUB_A | CSI300_BH | +0.061 | -0.375 | +0.626 | True |
| SUB_B | CN_60_40 | +0.600 | +0.090 | +1.174 | False |
| SUB_B | CSI300_BH | +0.656 | +0.146 | +1.222 | False |

Where the CI includes 0 the difference is **not statistically distinguishable** from zero: FULL vs CN_60_40; FULL vs CSI300_BH; SUB_A vs CN_60_40; SUB_A vs CSI300_BH.
CI excludes 0: SUB_B vs CN_60_40; SUB_B vs CSI300_BH.

## Deviations and data notes

- **Data source (implementation note, not a protocol change):** Eastmoney `fund_etf_hist_em` (hfq) refused connections; bars are Sina unadjusted OHLC (`ak.fund_etf_hist_sina`).
- **Self-built adjustment:** total-return factor reinvesting each cash distribution at the ex-date close (Sina `hfq.js` cumulative dividend `u`) and applying share-conversion multipliers (`s`); same-day factor on open and close.
- **Conversion alignment:** Sina dates 510500's 2022 conversion at the record date 2022-08-26; the multiplier is applied on the bar (within 3) whose close ratio best matches (2022-08-29). The 2015-04-15 conversion aligns on its date.
- **Sharpe annualisation sqrt(252)** (v2 convention); SUB_A/SUB_B are slices of one continuous FULL run.
- **Missing bars and their resolution (amendment 1, rule 1):**
  - 510500 2015-04-13: SUSPENSION (no bar in independent source; source tried: Tencent ak.stock_zh_a_hist_tx (unadjusted); Eastmoney unreachable)
  - 510500 2015-04-14: SUSPENSION (no bar in independent source; source tried: Tencent ak.stock_zh_a_hist_tx (unadjusted); Eastmoney unreachable)
  - 159915 2021-02-08: SUSPENSION (no bar in independent source; source tried: Tencent ak.stock_zh_a_hist_tx (unadjusted); Eastmoney unreachable)
- **Postponed rebalances (rule 3), all strategies and cost settings:** 0
- **Carry-forward valuation days (suspended while held):** {"primary/CAP_DEF_M": {"510500": 2, "159915": 1}, "primary/BASE_M_CN": {"510500": 2, "159915": 1}, "secondary_10_10/CAP_DEF_M": {"510500": 2, "159915": 1}, "secondary_10_10/BASE_M_CN": {"510500": 2, "159915": 1}}
- **SHIBOR tail:** the last 15 rows (2026-09-21 to 2026-10-16) are flat at 1.43; 7 of them are on or before score_end. Plausible: the preceding 90 rows ranged 1.401 to 1.440 (3M SHIBOR has sat near 1.43 for months and the flat run is the same level at 2-decimal precision), and the 1M/6M tenors in the cross-check source still move. Data not altered.

## Data

Missing bars inside the scoring sample: {"510300": 0, "510050": 0, "510500": 2, "159915": 1, "518880": 0, "511010": 0}
Missing bars in the whole data range (warm-up included): {"510300": 0, "510050": 0, "510500": 2, "159915": 1, "518880": 0, "511010": 0}
Bars on non-session days: {}

