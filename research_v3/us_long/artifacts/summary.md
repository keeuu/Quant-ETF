# us_long: out-of-period test of frozen CAP_DEF_M (1972-2005 proxies) and CRASH_WEEKLY_EXIT challenger

Protocols (hash-verified at run time): protocol_us_long.json `e7d63499...`, protocol_challenger_crash.json `eacd745b...`. Costs 5+5 bps, DTB3 cash, signal at close t, trade at close t+1.

## Outcomes

- **Frozen CAP_DEF_M (protocol_us_long): SUPPORTED**
  - FULL xSharpe > US_60_40: True
  - FULL MDD < SPY_BH MDD: True
  - SUB_A xSharpe > US_60_40: True; SUB_B: True
- **CRASH_WEEKLY_EXIT (protocol_challenger_crash): NOT_SUPPORTED**
  - FULL MDD lower by >= 3.0 pp: False (reduction 2.30 pp)
  - FULL xSharpe not lower by more than 0.05: True (change +0.022)
  - MDD not higher than frozen in SUB_A: True; in SUB_B: False

## Data

Sessions 1971-01-04..2005-12-30 (8836); first warm monthly signal 1972-02-29 -> score_start 1972-03-01; score_end 2005-12-30.

| proxy | first date | forward-filled sessions (valuation) | weekly signal dates with no observation |
|---|---|---|---|
| SPY | 1971-01-04 | 0 | 0 |
| QQQ | 1971-02-05 | 0 | 0 |
| IWM | 1971-01-04 | 0 | 0 |
| EFA | 1990-07-02 | 0 | 0 |
| EEM | unavailable | 0 | None |
| GLD | 1971-01-04 | 204 | 33 |
| TLT | 1977-02-15 | 83 | 6 |
| IEF | 1971-01-04 | 105 | 6 |

Frozen-rule executions at a forward-filled price: 4. Weekly events 1826, monthly events 420.

## Frozen rule, BASE (context only) and benchmarks (CRASH_WEEKLY_EXIT shown in the next section)

### FULL (1972-03-01 to 2005-12-30)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 13.41% | 8.63% | 0.774 | 23.43% | 1980-01-21..1980-03-27 | -2.53% (1990) | 2.90 | 18.64% | 35.66% |
| BASE_M | 13.43% | 13.19% | 0.546 | 35.65% | 1973-06-05..1974-07-05 | -13.93% (1987) | 2.53 | 4.52% | 25.83% |
| SPY_BH | 10.99% | 15.16% | 0.351 | 49.23% | 2000-03-24..2002-10-09 | -27.80% (1974) | 0.03 | 0.00% | 0.00% |
| US_60_40 | 10.23% | 9.92% | 0.399 | 31.75% | 1973-01-11..1974-10-03 | -15.89% (1974) | 0.13 | 0.00% | 39.99% |
| SPY_SMA10 | 10.61% | 11.73% | 0.385 | 32.30% | 1987-08-25..1987-10-26 | -16.87% (1973) | 1.57 | 27.29% | 0.00% |

### SUB_A (1972-03-01 to 1989-12-29)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 15.77% | 8.96% | 0.767 | 23.43% | 1980-01-21..1980-03-27 | 2.92% (1975) | 2.83 | 24.96% | 34.65% |
| BASE_M | 15.27% | 14.42% | 0.492 | 35.65% | 1973-06-05..1974-07-05 | -13.93% (1987) | 2.51 | 7.63% | 22.39% |
| SPY_BH | 11.38% | 14.49% | 0.255 | 48.16% | 1973-01-11..1974-10-03 | -27.80% (1974) | 0.06 | 0.00% | 0.00% |
| US_60_40 | 10.68% | 10.03% | 0.250 | 31.75% | 1973-01-11..1974-10-03 | -15.89% (1974) | 0.16 | 0.00% | 39.98% |
| SPY_SMA10 | 11.61% | 11.63% | 0.303 | 32.30% | 1987-08-25..1987-10-26 | -16.87% (1973) | 1.51 | 28.50% | 0.00% |

### SUB_B (1990-01-02 to 2005-12-30)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 10.85% | 8.25% | 0.784 | 17.37% | 2000-03-24..2000-05-23 | -2.53% (1990) | 2.97 | 11.59% | 36.79% |
| BASE_M | 11.43% | 11.67% | 0.628 | 30.62% | 2000-03-10..2000-10-04 | -10.33% (2000) | 2.54 | 1.04% | 29.66% |
| SPY_BH | 10.57% | 15.88% | 0.450 | 49.23% | 2000-03-24..2002-10-09 | -20.38% (2002) | 0.00 | 0.00% | 0.00% |
| US_60_40 | 9.74% | 9.81% | 0.571 | 24.17% | 2000-09-01..2002-07-23 | -6.98% (2002) | 0.10 | 0.00% | 40.01% |
| SPY_SMA10 | 9.51% | 11.85% | 0.474 | 24.78% | 2000-03-24..2000-10-12 | -16.09% (1990) | 1.63 | 25.94% | 0.00% |

## Calendar-year returns, key years

Scoring starts 1972-03-01, so 1972 is a partial year; 1973 onward are full years. 'SPY proxy (Jan-Dec)' is the proxy index over the whole calendar year.

| year | CAP_DEF_M | SPY_BH | US_60_40 | CRASH_WEEKLY_EXIT | SPY proxy (Jan-Dec) |
|---|---|---|---|---|---|
| 1973 | 8.25% | -19.14% | -10.41% | 13.21% | -19.14% |
| 1974 | 22.26% | -27.80% | -15.89% | 22.26% | -27.80% |
| 1987 | 5.20% | 1.55% | 1.37% | 3.36% | 1.55% |
| 2000 | -2.49% | -11.69% | -0.80% | -2.52% | -11.69% |
| 2001 | -0.24% | -11.31% | -4.01% | 5.34% | -11.31% |
| 2002 | 7.29% | -20.38% | -6.98% | 12.27% | -20.38% |

All years: calendar_year_returns.csv.

## Bootstrap (90% CI of excess-Sharpe difference; block 21, 2000 resamples, seed 20261019)

| partition | strategy | reference | diff | CI90 lo | CI90 hi | includes 0 |
|---|---|---|---|---|---|---|
| FULL | CAP_DEF_M | US_60_40 | +0.375 | +0.078 | +0.662 | False |
| FULL | CAP_DEF_M | SPY_BH | +0.424 | +0.111 | +0.733 | False |
| FULL | CRASH_WEEKLY_EXIT | CAP_DEF_M | +0.022 | -0.037 | +0.086 | True |
| SUB_A | CAP_DEF_M | US_60_40 | +0.518 | +0.082 | +0.958 | False |
| SUB_A | CAP_DEF_M | SPY_BH | +0.513 | +0.056 | +0.972 | False |
| SUB_A | CRASH_WEEKLY_EXIT | CAP_DEF_M | +0.005 | -0.043 | +0.057 | True |
| SUB_B | CAP_DEF_M | US_60_40 | +0.213 | -0.184 | +0.582 | True |
| SUB_B | CAP_DEF_M | SPY_BH | +0.334 | -0.094 | +0.735 | True |
| SUB_B | CRASH_WEEKLY_EXIT | CAP_DEF_M | +0.045 | -0.074 | +0.165 | True |

Rows whose CI includes 0 are not statistically distinguishable from zero: FULL CRASH_WEEKLY_EXIT vs CAP_DEF_M; SUB_A CRASH_WEEKLY_EXIT vs CAP_DEF_M; SUB_B CAP_DEF_M vs US_60_40; SUB_B CAP_DEF_M vs SPY_BH; SUB_B CRASH_WEEKLY_EXIT vs CAP_DEF_M.

## Challenger: CRASH_WEEKLY_EXIT vs frozen CAP_DEF_M (same data)

### FULL (1972-03-01 to 2005-12-30)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 13.41% | 8.63% | 0.774 | 23.43% | 1980-01-21..1980-03-27 | -2.53% (1990) | 2.90 | 18.64% | 35.66% |
| CRASH_WEEKLY_EXIT | 13.49% | 8.46% | 0.797 | 21.13% | 1980-01-21..1980-03-27 | -2.52% (2000) | 3.25 | 19.31% | 36.71% |

### SUB_A (1972-03-01 to 1989-12-29)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 15.77% | 8.96% | 0.767 | 23.43% | 1980-01-21..1980-03-27 | 2.92% (1975) | 2.83 | 24.96% | 34.65% |
| CRASH_WEEKLY_EXIT | 15.73% | 8.85% | 0.772 | 21.13% | 1980-01-21..1980-03-27 | 2.90% (1975) | 3.03 | 25.42% | 35.13% |

### SUB_B (1990-01-02 to 2005-12-30)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 10.85% | 8.25% | 0.784 | 17.37% | 2000-03-24..2000-05-23 | -2.53% (1990) | 2.97 | 11.59% | 36.79% |
| CRASH_WEEKLY_EXIT | 11.06% | 8.00% | 0.829 | 18.32% | 2000-03-24..2000-05-10 | -2.52% (2000) | 3.49 | 12.48% | 38.48% |

Weekly exit events: 48 (assets exited: 49); turnover/yr 2.90 -> 3.25 (+0.35). Event list: crash_weekly_exits.csv.

Calendar-year difference (CRASH minus frozen, pp), years with |diff| >= 1 pp: 1972: -1.7, 1973: +5.0, 1976: -1.2, 1987: -1.8, 1992: -4.7, 1993: -1.5, 1996: -1.7, 1997: +1.5, 1998: +1.3, 2001: +5.6, 2002: +5.0, 2004: -1.9

## Secondary, information-only challenger runs (cannot support the challenger)

### US 2006-02..2020-12 (research_v2 snapshot, real ETFs, next-open execution)

partitions: SUB_A = train (2006-02..2015-12), SUB_B = validation (2016..2020); one continuous run

**FULL** (2006-02-01 to 2020-12-31)

| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr |
|---|---|---|---|---|---|---|
| CAP_DEF_M | 9.87% | 12.50% | 0.727 | 20.11% | -7.66% | 3.41 |
| CRASH_WEEKLY_EXIT | 8.84% | 11.86% | 0.681 | 18.89% | -11.13% | 4.02 |
| SPY_BH | 9.65% | 20.12% | 0.504 | 55.19% | -36.80% | 0.07 |
| US_60_40 | 8.31% | 10.90% | 0.687 | 32.54% | -17.92% | 0.19 |

**SUB_A** (2006-02-01 to 2015-12-31)

| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr |
|---|---|---|---|---|---|---|
| CAP_DEF_M | 8.74% | 13.56% | 0.606 | 20.11% | -7.66% | 3.35 |
| CRASH_WEEKLY_EXIT | 7.26% | 12.73% | 0.529 | 18.89% | -11.13% | 4.04 |
| SPY_BH | 7.00% | 20.73% | 0.378 | 55.19% | -36.80% | 0.10 |
| US_60_40 | 6.87% | 11.18% | 0.553 | 32.54% | -17.92% | 0.22 |

**SUB_B** (2016-01-04 to 2020-12-31)

| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr |
|---|---|---|---|---|---|---|
| CAP_DEF_M | 12.16% | 10.09% | 1.075 | 13.05% | -2.18% | 3.54 |
| CRASH_WEEKLY_EXIT | 12.05% | 9.93% | 1.082 | 14.42% | 0.48% | 3.99 |
| SPY_BH | 15.12% | 18.87% | 0.781 | 33.72% | -4.57% | 0.00 |
| US_60_40 | 11.26% | 10.30% | 0.977 | 18.86% | -1.97% | 0.12 |

Criteria (information only): FULL MDD reduction 1.23 pp (>=3: False); FULL xSharpe change -0.047 (ok: True); MDD not higher SUB_A True, SUB_B False -> would be NOT_SUPPORTED. Weekly exit events: 31.

Bootstrap (CRASH vs frozen):

| partition | strategy | reference | diff | CI90 lo | CI90 hi | includes 0 |
|---|---|---|---|---|---|---|
| FULL | CRASH_WEEKLY_EXIT | CAP_DEF_M | -0.047 | -0.151 | +0.055 | True |
| SUB_A | CRASH_WEEKLY_EXIT | CAP_DEF_M | -0.077 | -0.225 | +0.056 | True |
| SUB_B | CRASH_WEEKLY_EXIT | CAP_DEF_M | +0.007 | -0.124 | +0.159 | True |

### CN 2014-09..2026-09 (cn_transfer data, amendment-1 suspension rules)

partitions: SUB_A = ..2020-12-31, SUB_B = 2021..; one continuous run, amendment-1 suspension rules

**FULL** (2014-09-01 to 2026-09-30)

| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr |
|---|---|---|---|---|---|---|
| CAP_DEF_M | 10.22% | 13.91% | 0.590 | 33.84% | -0.93% | 2.47 |
| CRASH_WEEKLY_EXIT | 9.64% | 13.23% | 0.572 | 29.48% | -1.68% | 2.95 |
| CN_60_40 | 6.15% | 13.42% | 0.316 | 28.50% | -12.75% | 0.22 |
| CSI300_BH | 7.02% | 22.62% | 0.299 | 45.44% | -24.16% | 0.08 |

**SUB_A** (2014-09-01 to 2020-12-31)

| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr |
|---|---|---|---|---|---|---|
| CAP_DEF_M | 11.82% | 15.02% | 0.612 | 33.84% | -0.93% | 2.57 |
| CRASH_WEEKLY_EXIT | 13.56% | 14.22% | 0.750 | 29.48% | -0.74% | 2.83 |
| CN_60_40 | 10.91% | 15.37% | 0.546 | 28.50% | -12.75% | 0.30 |
| CSI300_BH | 14.97% | 25.96% | 0.551 | 45.44% | -24.16% | 0.16 |

**SUB_B** (2021-01-04 to 2026-09-30)

| strategy | CAGR | vol | xSharpe | MDD | worst yr | turnover/yr |
|---|---|---|---|---|---|---|
| CAP_DEF_M | 8.49% | 12.58% | 0.567 | 13.50% | -0.30% | 2.36 |
| CRASH_WEEKLY_EXIT | 5.48% | 12.03% | 0.345 | 13.93% | -1.68% | 3.09 |
| CN_60_40 | 1.14% | 10.84% | -0.032 | 24.34% | -11.31% | 0.12 |
| CSI300_BH | -1.10% | 18.22% | -0.088 | 42.18% | -20.13% | 0.00 |

Criteria (information only): FULL MDD reduction 4.36 pp (>=3: True); FULL xSharpe change -0.018 (ok: True); MDD not higher SUB_A True, SUB_B False -> would be NOT_SUPPORTED. Weekly exit events: 20.

Frozen-rule NAV vs the committed cn_transfer artifact: max abs difference 5.82e-11.

Postponed rebalances by strategy: {"CAP_DEF_M": 0, "CN_60_40": 0, "CSI300_BH": 0, "CRASH_WEEKLY_EXIT": 0}.

Bootstrap (CRASH vs frozen):

| partition | strategy | reference | diff | CI90 lo | CI90 hi | includes 0 |
|---|---|---|---|---|---|---|
| FULL | CRASH_WEEKLY_EXIT | CAP_DEF_M | -0.018 | -0.189 | +0.119 | True |
| SUB_A | CRASH_WEEKLY_EXIT | CAP_DEF_M | +0.138 | +0.005 | +0.293 | False |
| SUB_B | CRASH_WEEKLY_EXIT | CAP_DEF_M | -0.223 | -0.532 | +0.019 | True |

## Sensitivity (information only): signals see forward-filled values

The primary run follows the protocol literally (a proxy without a new observation on a session shows NaN to the signals; the fill is used for valuation only). Here signals read the forward-filled series instead.

### FULL (1972-03-01 to 2005-12-30)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 13.26% | 8.93% | 0.737 | 23.43% | 1980-01-21..1980-03-27 | -6.01% (2000) | 2.81 | 15.03% | 34.77% |
| CRASH_WEEKLY_EXIT | 13.34% | 8.79% | 0.755 | 21.13% | 1980-01-21..1980-03-27 | -6.05% (2000) | 3.13 | 15.65% | 35.86% |
| SPY_BH | 10.99% | 15.16% | 0.351 | 49.23% | 2000-03-24..2002-10-09 | -27.80% (1974) | 0.03 | 0.00% | 0.00% |
| US_60_40 | 10.23% | 9.92% | 0.399 | 31.75% | 1973-01-11..1974-10-03 | -15.89% (1974) | 0.13 | 0.00% | 39.99% |

### SUB_A (1972-03-01 to 1989-12-29)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 16.39% | 9.35% | 0.797 | 23.43% | 1980-01-21..1980-03-27 | 2.37% (1988) | 2.54 | 20.58% | 34.98% |
| CRASH_WEEKLY_EXIT | 16.35% | 9.26% | 0.799 | 21.13% | 1980-01-21..1980-03-27 | 1.58% (1988) | 2.71 | 20.96% | 35.55% |
| SPY_BH | 11.38% | 14.49% | 0.255 | 48.16% | 1973-01-11..1974-10-03 | -27.80% (1974) | 0.06 | 0.00% | 0.00% |
| US_60_40 | 10.68% | 10.03% | 0.250 | 31.75% | 1973-01-11..1974-10-03 | -15.89% (1974) | 0.16 | 0.00% | 39.98% |

### SUB_B (1990-01-02 to 2005-12-30)

| strategy | CAGR | vol | xSharpe | MDD | MDD peak..trough | worst yr | turnover/yr | cash wt | bond wt |
|---|---|---|---|---|---|---|---|---|---|
| CAP_DEF_M | 9.88% | 8.43% | 0.665 | 19.69% | 2000-03-10..2000-05-23 | -6.01% (2000) | 3.10 | 8.84% | 34.53% |
| CRASH_WEEKLY_EXIT | 10.09% | 8.22% | 0.702 | 20.62% | 2000-03-10..2000-05-10 | -6.05% (2000) | 3.60 | 9.73% | 36.22% |
| SPY_BH | 10.57% | 15.88% | 0.450 | 49.23% | 2000-03-24..2002-10-09 | -20.38% (2002) | 0.00 | 0.00% | 0.00% |
| US_60_40 | 9.74% | 9.81% | 0.571 | 24.17% | 2000-09-01..2002-07-23 | -6.98% (2002) | 0.10 | 0.00% | 40.01% |

Outcomes in this sensitivity: frozen SUPPORTED, challenger NOT_SUPPORTED.

## Deviations from the protocols and data notes

1. **EEM proxy unavailable.** The Ken French library has no daily Emerging Markets file (only monthly 'Emerging_5_Factors', etc.). Per the protocol ('else unavailable') EEM is never in the universe; INTL_EQ = EFA proxy only, from 1990-07-02.
2. **Gold source.** FRED no longer serves the London fix series (GOLDAMGBD228NLBM / GOLDPMGBD228NLBM return an error page); the LBMA JSON feed is behind a Cloudflare challenge and stooq requires a JavaScript challenge (not bypassed). Used: GoldPrice.com daily CSV `gold-price-history.csv` (CC BY 4.0; per the site LBMA fixes through about 2014/15, Metals.dev closes after; AM/PM fix not specified), first date 1968-04-01. Only 1971-2005 is used (LBMA fixes).
3. **QQQ proxy is price-only** (^IXIC close via yfinance, first 1971-02-05), as declared in the protocol.
4. **Execution lag.** Signals at close t, trades at the close of the next session (declared). Implemented by an adapter engine (engine_ul.py) with exec price = valuation price of the execution session; with open execution on the research_v2 snapshot it reproduces run_backtest_v3 exactly (tests).
5. **Missing observations.** Reading the protocol literally, signals see NaN where a proxy has no observation on a session (gold London holidays, bond-market holidays), valuation uses the forward-filled level; fills are counted above. A forward-filled-signal sensitivity is reported and does not change either outcome.
6. **Synthetic bonds** use consecutive observed yield dates with accrual y_{t-1}/252 per observation (no roll-down); on bond-market holidays the index is carried forward. FRED's DGS30 currently contains values for 2002-02..2006-02 (the period when Treasury discontinued the 30-year); they were used as published, no gap handling was needed. TLT proxy starts 1977-02-15.
7. **Index base.** Return-based proxies start at 100 on their first session; ^IXIC and gold use their own levels.
8. **score_start** (not defined precisely): first COMPLETE monthly row where every asset with an observation on or before the source weekly signal has finite 12M momentum and a computable 40-week SMA. Result 1972-03-01 (signal 1972-02-29). Assets that start later (TLT 1977, EFA 1990) enter the ranking only once their own 12M/40-week windows are full.
9. **Partitions** SUB_A / SUB_B are slices of one continuous FULL run (as in the cn_transfer study), not separate restarts. SUB_B uses 1990-01-01..2005-12-30 (last session 2005-12-30; the Dec-30 monthly signal executes in 2006, outside the sample).
10. **Benchmarks** reuse research_v3.step4.benchmark_frames on the matched monthly schedule (US_60_40 = 60% SPY / 40% IEF proxy; SPY_BH buys at the first monthly execution; SPY_SMA10 as in step 4).
11. **CRASH_WEEKLY_EXIT implementation choices** (rule text unchanged): a weekly signal that coincides with a monthly signal session is treated as monthly; 'currently held' = target holdings after the last executed full/exit row; an exit sells only the failing holding at the next execution (close t+1 in the primary run) and buys the DEF choice with the proceeds, costs 5+5 bps on gross traded notional (self-financing, same convention as the ledger); remaining positions are not rebalanced and drift until the next monthly signal. A trend failure caused by missing data (no SMA) counts as a failure, as in the frozen test.
12. **Secondary US 2006-2020** is one continuous run from 2006-02-01 (not the separate train / validation runs of earlier steps), real adj_open execution; SUB_A = train period, SUB_B = validation period. **Secondary CN** uses the cn_transfer data, calendar and amendment-1 suspension rules; an exit postponed by a suspension waits in a queue and is superseded only by the next monthly row.
13. Bootstrap CIs use the research_v3 stationary bootstrap with the protocol seed; the challenger CI is vs the frozen rule.
