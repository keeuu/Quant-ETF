# HYBRID phase 1 (DEV sanity check)

Protocol sha256 6ec329269e0e9a75791f7be966d6071a1f22505912035d7da5f95f77f0310997 verified at run time. Window 2020-07-13..2024-09-30; first execution 2020-08-03. Holdout not loaded. No decision depends on this phase.

## DEV metrics: Primary cost 2.5+10 bp

| strategy | CAGR | vol | xSharpe | MDD | turnover/yr | cost drag | eq wt | gold wt | bond wt | cash wt | round trips (fills) | lim-blk b/s | postponed days |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HYBRID | 3.27% | 11.33% | 0.140 | 17.16% | 2.42 | 0.55% | 26.4% | 21.9% | 41.1% | 10.6% | 51 (400) | 0/0 | 0 |
| CAP_DEF_M_CN | 3.07% | 9.21% | 0.126 | 12.92% | 1.89 | 0.48% | 26.4% | 21.9% | 41.1% | 10.6% | 12 (172) | 0/0 | 0 |
| CSI300_BH | -1.38% | 17.82% | -0.127 | 42.18% | 0.13 | 0.03% | 98.5% | 0.0% | 0.0% | 1.5% | 0 (1) | 0/0 | 0 |
| CN_60_40 | 0.80% | 10.60% | -0.095 | 24.35% | 0.23 | 0.06% | 58.9% | 0.0% | 39.5% | 1.6% | 0 (138) | 0/0 | 0 |
| EW_SECTOR | -4.95% | 19.81% | -0.288 | 44.10% | 0.96 | 0.19% | 98.5% | 0.0% | 0.0% | 1.5% | 64 (5109) | 0/0 | 0 |

## DEV metrics: Sensitivity cost 2.5+20 bp

| strategy | CAGR | vol | xSharpe | MDD | turnover/yr | cost drag | eq wt | gold wt | bond wt | cash wt | round trips (fills) | lim-blk b/s | postponed days |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HYBRID | 2.77% | 11.33% | 0.095 | 17.39% | 2.42 | 1.05% | 26.4% | 21.9% | 41.1% | 10.6% | 51 (413) | 0/0 | 0 |
| CAP_DEF_M_CN | 2.68% | 9.21% | 0.083 | 12.95% | 1.88 | 0.87% | 26.4% | 21.9% | 41.1% | 10.6% | 12 (170) | 0/0 | 0 |
| CSI300_BH | -1.40% | 17.82% | -0.128 | 42.18% | 0.13 | 0.05% | 98.5% | 0.0% | 0.0% | 1.5% | 0 (1) | 0/0 | 0 |
| CN_60_40 | 0.75% | 10.60% | -0.100 | 24.41% | 0.23 | 0.11% | 58.9% | 0.0% | 39.5% | 1.5% | 0 (140) | 0/0 | 0 |
| EW_SECTOR | -5.13% | 19.80% | -0.298 | 44.33% | 0.96 | 0.37% | 98.5% | 0.0% | 0.0% | 1.5% | 64 (5246) | 0/0 | 0 |

## HYBRID minus CAP_DEF_M_CN (DEV xSharpe)

- primary: +0.014 (bootstrap 90% CI [-0.502, +0.553])
- sensitivity: +0.012 (CI [-0.504, +0.554])

## Checks

```
{
 "independent_recompute": {
  "2021-01-29": {
   "match": true,
   "E": 0.6666666666666666,
   "n_sector": 5,
   "sum_weights": 0.6666666666666666,
   "cash": 0.33333333333333337
  },
  "2023-10-31": {
   "match": true,
   "E": 0,
   "n_sector": 5,
   "sum_weights": 0.6666666666666666,
   "cash": 0.33333333333333337
  },
  "2024-07-31": {
   "match": true,
   "E": 0.3333333333333333,
   "n_sector": 5,
   "sum_weights": 1.0,
   "cash": 0.0
  }
 },
 "replacement_off_max_abs_nav_diff_vs_CAP_DEF_M_CN": 0.0,
 "first_execution": "2020-08-03",
 "n_monthly_rebalances_in_dev": 50
}
```
