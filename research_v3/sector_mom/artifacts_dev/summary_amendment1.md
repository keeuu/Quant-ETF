# sector_mom amendment 1: S5-S7 on the development period

Amendment sha256 f8c397a81c142e9339dc2e1f3cb44e61359b619fc3221239a44d5b7cb12af5ef (verified at run time). Holdout not loaded. S5-S7 first execution: **2020-08-03** (common DEV window 2020-07-13..2024-09-30; before the first execution S5-S7 sit in SHIBOR cash). S5-S7 eligibility: age >= 253 sessions.

## Selection over S1-S7

**No strategy qualifies.**

Qualifiers: []

| strategy | cost | C1 DEV xSharpe | C1 | C2 DEV MDD | C2 | C3 A and B vs EW | C3 |
|---|---|---|---|---|---|---|---|
| S1_WEEKLY_MOM | primary | -0.474 (EW -0.269, CSI -0.101) | False | 60.02% (CSI 43.10%) | False | A -0.477/-0.191; B -0.471/-0.351 | False |
| S1_WEEKLY_MOM | sens | -0.668 (EW -0.279, CSI -0.102) | False | 67.59% (CSI 43.10%) | False | A -0.656/-0.204; B -0.681/-0.358 | False |
| S2_MA10_EXIT_WEEKLY_REENTRY | primary | -0.959 (EW -0.269, CSI -0.101) | False | 68.52% (CSI 43.10%) | False | A -1.381/-0.191; B -0.511/-0.351 | False |
| S2_MA10_EXIT_WEEKLY_REENTRY | sens | -1.263 (EW -0.279, CSI -0.102) | False | 75.61% (CSI 43.10%) | False | A -1.671/-0.204; B -0.830/-0.358 | False |
| S3_MA5_EXIT_WEEKLY_REENTRY | primary | -1.232 (EW -0.269, CSI -0.101) | False | 70.11% (CSI 43.10%) | False | A -1.970/-0.191; B -0.530/-0.351 | False |
| S3_MA5_EXIT_WEEKLY_REENTRY | sens | -1.628 (EW -0.279, CSI -0.102) | False | 77.57% (CSI 43.10%) | False | A -2.379/-0.204; B -0.916/-0.358 | False |
| S4_MA10_EXIT_DAILY_REENTRY | primary | -1.173 (EW -0.269, CSI -0.101) | False | 75.26% (CSI 43.10%) | False | A -1.448/-0.191; B -0.885/-0.351 | False |
| S4_MA10_EXIT_DAILY_REENTRY | sens | -1.528 (EW -0.279, CSI -0.102) | False | 81.90% (CSI 43.10%) | False | A -1.785/-0.204; B -1.259/-0.358 | False |
| S5_IND_MOM_12_1 | primary | -0.268 (EW -0.269, CSI -0.101) | False | 58.10% (CSI 43.10%) | False | A 0.284/-0.191; B -0.895/-0.351 | False |
| S5_IND_MOM_12_1 | sens | -0.294 (EW -0.279, CSI -0.102) | False | 58.83% (CSI 43.10%) | False | A 0.260/-0.204; B -0.924/-0.358 | False |
| S6_IND_MOM_6_1 | primary | -0.307 (EW -0.269, CSI -0.101) | False | 62.96% (CSI 43.10%) | False | A 0.136/-0.191; B -0.776/-0.351 | False |
| S6_IND_MOM_6_1 | sens | -0.353 (EW -0.279, CSI -0.102) | False | 64.28% (CSI 43.10%) | False | A 0.091/-0.204; B -0.822/-0.358 | False |
| S7_DUAL_MOM_12_1 | primary | -0.314 (EW -0.269, CSI -0.101) | False | 59.82% (CSI 43.10%) | False | A 0.284/-0.191; B -1.009/-0.351 | False |
| S7_DUAL_MOM_12_1 | sens | -0.341 (EW -0.279, CSI -0.102) | False | 60.49% (CSI 43.10%) | False | A 0.260/-0.204; B -1.042/-0.358 | False |

## DEV main table (primary cost, 1,000,000 CNY)

| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr | cost drag | round trips | fills | win | avg hold d | mean cash | lim-blk b/s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S1_WEEKLY_MOM | -12.55% | 26.81% | -0.474 | 60.02% | -27.28% (2022) | 24.84 | 5.66% | 486 | 2488 | 35.4% | 14.9 | 4.35% | 2/0 |
| S5_IND_MOM_12_1 | -7.90% | 27.10% | -0.268 | 58.10% | -29.25% (2023) | 3.65 | 0.74% | 69 | 494 | 33.3% | 103.1 | 1.64% | 0/0 |
| S6_IND_MOM_6_1 | -8.76% | 26.90% | -0.307 | 62.96% | -39.55% (2022) | 5.87 | 1.33% | 117 | 606 | 42.7% | 63.2 | 1.61% | 0/0 |
| S7_DUAL_MOM_12_1 | -8.87% | 26.84% | -0.314 | 59.82% | -30.25% (2023) | 3.61 | 0.73% | 69 | 478 | 29.0% | 97.7 | 6.54% | 0/0 |
| CSI300_BH | -1.34% | 19.31% | -0.101 | 43.10% | -21.68% (2022) | 0.13 | 0.03% | 0 | 1 | n/a | nan | 0.00% | 0/0 |
| EW_SECTOR | -4.85% | 20.37% | -0.269 | 44.10% | -23.20% (2022) | 0.99 | 0.20% | 64 | 5134 | 18.8% | 248.2 | 0.07% | 0/0 |

## xSharpe by period (primary / sensitivity)

| strategy | DEV | DEV_A | DEV_B |
|---|---|---|---|
| S1_WEEKLY_MOM | -0.474 / -0.668 | -0.477 / -0.656 | -0.471 / -0.681 |
| S5_IND_MOM_12_1 | -0.268 / -0.294 | 0.284 / 0.260 | -0.895 / -0.924 |
| S6_IND_MOM_6_1 | -0.307 / -0.353 | 0.136 / 0.091 | -0.776 / -0.822 |
| S7_DUAL_MOM_12_1 | -0.314 / -0.341 | 0.284 / 0.260 | -1.009 / -1.042 |
| CSI300_BH | -0.101 / -0.102 | -0.240 / -0.243 | 0.056 / 0.056 |
| EW_SECTOR | -0.269 / -0.279 | -0.191 / -0.204 | -0.351 / -0.358 |

## Sensitivity cost DEV and 100k lots DEV (primary)

| strategy | sens CAGR | sens xSharpe | sens MDD | lots CAGR | lots xSharpe | lots MDD |
|---|---|---|---|---|---|---|
| S1_WEEKLY_MOM | -16.83% | -0.668 | 67.59% | -14.27% | -0.552 | 63.15% |
| S5_IND_MOM_12_1 | -8.53% | -0.294 | 58.83% | -8.15% | -0.278 | 58.33% |
| S6_IND_MOM_6_1 | -9.82% | -0.353 | 64.28% | -9.01% | -0.319 | 63.32% |
| S7_DUAL_MOM_12_1 | -9.51% | -0.341 | 60.49% | -9.10% | -0.323 | 60.04% |
| CSI300_BH | -1.37% | -0.102 | 43.10% | -1.33% | -0.101 | 42.94% |
| EW_SECTOR | -5.03% | -0.279 | 44.33% | -6.79% | -0.380 | 47.36% |

## Bootstrap 90% CI of xSharpe difference (primary)

| period | strategy | vs | diff | lo | hi | incl 0 |
|---|---|---|---|---|---|---|
| DEV | S5_IND_MOM_12_1 | EW_SECTOR | +0.001 | -0.566 | +0.569 | True |
| DEV | S5_IND_MOM_12_1 | CSI300_BH | -0.167 | -0.866 | +0.599 | True |
| DEV | S6_IND_MOM_6_1 | EW_SECTOR | -0.039 | -0.648 | +0.596 | True |
| DEV | S6_IND_MOM_6_1 | CSI300_BH | -0.207 | -0.935 | +0.594 | True |
| DEV | S7_DUAL_MOM_12_1 | EW_SECTOR | -0.045 | -0.640 | +0.542 | True |
| DEV | S7_DUAL_MOM_12_1 | CSI300_BH | -0.213 | -0.933 | +0.573 | True |
| DEV_A | S5_IND_MOM_12_1 | EW_SECTOR | +0.475 | -0.109 | +1.088 | True |
| DEV_A | S5_IND_MOM_12_1 | CSI300_BH | +0.524 | -0.352 | +1.481 | True |
| DEV_A | S6_IND_MOM_6_1 | EW_SECTOR | +0.327 | -0.445 | +1.100 | True |
| DEV_A | S6_IND_MOM_6_1 | CSI300_BH | +0.376 | -0.570 | +1.360 | True |
| DEV_A | S7_DUAL_MOM_12_1 | EW_SECTOR | +0.475 | -0.109 | +1.088 | True |
| DEV_A | S7_DUAL_MOM_12_1 | CSI300_BH | +0.524 | -0.352 | +1.481 | True |
| DEV_B | S5_IND_MOM_12_1 | EW_SECTOR | -0.543 | -1.416 | +0.378 | True |
| DEV_B | S5_IND_MOM_12_1 | CSI300_BH | -0.950 | -1.887 | +0.144 | True |
| DEV_B | S6_IND_MOM_6_1 | EW_SECTOR | -0.425 | -1.348 | +0.611 | True |
| DEV_B | S6_IND_MOM_6_1 | CSI300_BH | -0.832 | -1.835 | +0.395 | True |
| DEV_B | S7_DUAL_MOM_12_1 | EW_SECTOR | -0.658 | -1.621 | +0.318 | True |
| DEV_B | S7_DUAL_MOM_12_1 | CSI300_BH | -1.065 | -2.053 | +0.075 | True |

## PSR(0) and DSR (N = 7)

| cost | strategy | PSR(0) | DSR N=7 | SR0 ann |
|---|---|---|---|---|
| primary | S1_WEEKLY_MOM | 0.171 | 0.016 | 0.597 |
| primary | S2_MA10_EXIT_WEEKLY_REENTRY | 0.029 | 0.001 | 0.597 |
| primary | S3_MA5_EXIT_WEEKLY_REENTRY | 0.009 | 0.000 | 0.597 |
| primary | S4_MA10_EXIT_DAILY_REENTRY | 0.010 | 0.000 | 0.597 |
| primary | S5_IND_MOM_12_1 | 0.295 | 0.040 | 0.597 |
| primary | S6_IND_MOM_6_1 | 0.268 | 0.034 | 0.597 |
| primary | S7_DUAL_MOM_12_1 | 0.263 | 0.033 | 0.597 |
| primary | EW_SECTOR | 0.295 | n/a | 0.597 |
| primary | CSI300_BH | 0.420 | n/a | 0.597 |
| sens | S1_WEEKLY_MOM | 0.091 | 0.001 | 0.817 |
| sens | S2_MA10_EXIT_WEEKLY_REENTRY | 0.007 | 0.000 | 0.817 |
| sens | S3_MA5_EXIT_WEEKLY_REENTRY | 0.001 | 0.000 | 0.817 |
| sens | S4_MA10_EXIT_DAILY_REENTRY | 0.001 | 0.000 | 0.817 |
| sens | S5_IND_MOM_12_1 | 0.276 | 0.012 | 0.817 |
| sens | S6_IND_MOM_6_1 | 0.238 | 0.009 | 0.817 |
| sens | S7_DUAL_MOM_12_1 | 0.245 | 0.010 | 0.817 |
| sens | EW_SECTOR | 0.288 | n/a | 0.817 |
| sens | CSI300_BH | 0.419 | n/a | 0.817 |

## Independent checks

```
{
 "S5_zero_cost_engine_final_nav": 730807.4508504419,
 "S5_open_to_open_replay_final_nav": 730807.4508504418,
 "S5_rel_diff": -1.1102230246251565e-16,
 "S5_blocked_orders_zero_cost": {
  "limit_blocked_buy": 0,
  "limit_blocked_sell": 0,
  "t1_blocked_sell": 0,
  "nobar_blocked": 0,
  "cash_scaled_buys": 0
 },
 "S5_selection_recompute": {
  "2024-04-30": {
   "match": true,
   "n_deduped": 76,
   "mine": [
    "159887",
    "159930",
    "512800",
    "515220",
    "516110"
   ],
   "engine": [
    "159887",
    "159930",
    "512800",
    "515220",
    "516110"
   ]
  },
  "2023-01-31": {
   "match": true,
   "n_deduped": 69,
   "mine": [
    "159766",
    "159930",
    "159980",
    "512690",
    "515220"
   ],
   "engine": [
    "159766",
    "159930",
    "159980",
    "512690",
    "515220"
   ]
  },
  "2023-05-31": {
   "match": true,
   "n_deduped": 76,
   "mine": [
    "159869",
    "512980",
    "515230",
    "515400",
    "516510"
   ],
   "engine": [
    "159869",
    "512980",
    "515230",
    "515400",
    "516510"
   ]
  }
 }
}
```

## Notes

- S5-S7 run on the common DEV windows (not shifted to their own start); targets are computed on forward-filled closes, top 5 by score from the deduplicated eligible set (age >= 253), equal weight 1/5. S7 filters the already-selected top 5 by the SHIBOR cash return over the same 252-to-21 window; filtered slots stay cash.
- Weekly-rebalance mechanics (delta trades, sells first, blocked-order retry) are the same as S1.
