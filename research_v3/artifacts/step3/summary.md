# research_v3 step 3: cash slots, defensive slot, asset-class cap

Settings: V3_DEFAULT (^IRX cash, 5 bps cost + 5 bps slippage). Train and validation only. Means across trend lengths 36/40/44. Deltas vs BASE of the same cadence/partition. Sharpe rf=0; xSharpe = Sharpe of returns minus IRX daily accrual. Worst yr = worst calendar-year return (years with >=200 sessions); 2008 = calendar 2008 return (train only). IEF-def = mean weight held in IEF via defensive slots.

\* post-hoc candidate chosen after seeing step-2 results (not pre-registered as a hypothesis); weekly only.

## train / W

| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | worst yr | 2008 | cash wt | IEF-def | turnover/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 7.07% | +0.00 | 0.535 | +0.000 | 0.462 | +0.000 | 20.89% | +0.00 | -9.97% | 2.85% | 0.1% | 0.0% | 6.18 |
| CASHSLOT | 6.80% | -0.27 | 0.530 | -0.005 | 0.454 | -0.008 | 19.48% | -1.40 | -9.91% | -2.05% | 3.0% | 0.0% | 6.06 |
| DEF | 7.10% | +0.04 | 0.546 | +0.011 | 0.471 | +0.009 | 19.71% | -1.18 | -9.63% | 1.42% | 1.1% | 1.9% | 6.11 |
| CAP | 6.21% | -0.85 | 0.493 | -0.042 | 0.417 | -0.044 | 21.95% | +1.06 | -12.91% | 2.49% | 0.1% | 0.0% | 8.08 |
| CAP_DEF | 5.85% | -1.21 | 0.509 | -0.026 | 0.424 | -0.038 | 19.03% | -1.86 | -11.13% | -0.73% | 5.0% | 8.4% | 7.67 |
| CAND* | 7.07% | +0.00 | 0.559 | +0.024 | 0.481 | +0.019 | 19.75% | -1.13 | -11.97% | 9.22% | 0.1% | 0.0% | 7.76 |
| CAND_CAP_DEF* | 6.34% | -0.73 | 0.568 | +0.033 | 0.478 | +0.017 | 18.05% | -2.84 | -12.07% | 8.82% | 5.0% | 8.4% | 8.34 |

## train / M

| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | worst yr | 2008 | cash wt | IEF-def | turnover/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 9.22% | +0.00 | 0.649 | +0.000 | 0.578 | +0.000 | 25.65% | +0.00 | -8.90% | -3.83% | 0.0% | 0.0% | 2.50 |
| CASHSLOT | 9.06% | -0.16 | 0.647 | -0.001 | 0.576 | -0.003 | 22.96% | -2.69 | -8.03% | -5.10% | 2.3% | 0.0% | 2.50 |
| DEF | 9.37% | +0.15 | 0.663 | +0.014 | 0.592 | +0.014 | 22.96% | -2.69 | -7.38% | -1.63% | 0.9% | 1.3% | 2.57 |
| CAP | 8.05% | -1.17 | 0.597 | -0.052 | 0.524 | -0.055 | 29.72% | +4.07 | -10.21% | -3.80% | 0.0% | 0.0% | 3.40 |
| CAP_DEF | 7.97% | -1.25 | 0.635 | -0.014 | 0.554 | -0.024 | 22.58% | -3.07 | -8.17% | -2.55% | 4.0% | 7.7% | 3.42 |

## validation / W

| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | worst yr | 2008 | cash wt | IEF-def | turnover/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 8.54% | +0.00 | 0.726 | +0.000 | 0.635 | +0.000 | 16.00% | +0.00 | -3.20% | n/a | 0.5% | 0.0% | 6.91 |
| CASHSLOT | 9.03% | +0.49 | 0.782 | +0.057 | 0.688 | +0.054 | 16.00% | +0.00 | -0.65% | n/a | 2.8% | 0.0% | 6.33 |
| DEF | 9.37% | +0.83 | 0.807 | +0.081 | 0.713 | +0.078 | 16.00% | -0.00 | -0.47% | n/a | 2.0% | 0.9% | 6.44 |
| CAP | 9.50% | +0.95 | 0.816 | +0.090 | 0.722 | +0.088 | 18.01% | +2.01 | -10.06% | n/a | 0.5% | 0.0% | 8.48 |
| CAP_DEF | 9.88% | +1.33 | 0.983 | +0.258 | 0.872 | +0.237 | 14.17% | -1.83 | -2.02% | n/a | 7.1% | 5.8% | 7.50 |
| CAND* | 10.18% | +1.64 | 0.859 | +0.133 | 0.766 | +0.132 | 19.82% | +3.83 | 1.34% | n/a | 0.5% | 0.0% | 8.12 |
| CAND_CAP_DEF* | 8.17% | -0.37 | 0.840 | +0.114 | 0.726 | +0.092 | 13.58% | -2.41 | -2.09% | n/a | 7.1% | 5.8% | 8.31 |

## validation / M

| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | worst yr | 2008 | cash wt | IEF-def | turnover/yr |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 10.43% | +0.00 | 0.849 | +0.000 | 0.760 | +0.000 | 15.07% | +0.00 | -3.73% | n/a | 0.6% | 0.0% | 3.27 |
| CASHSLOT | 11.10% | +0.67 | 0.922 | +0.073 | 0.830 | +0.070 | 14.91% | -0.15 | -2.81% | n/a | 3.0% | 0.0% | 3.09 |
| DEF | 11.21% | +0.78 | 0.929 | +0.080 | 0.837 | +0.077 | 14.91% | -0.15 | -2.81% | n/a | 2.5% | 0.5% | 3.13 |
| CAP | 11.21% | +0.77 | 0.945 | +0.096 | 0.851 | +0.091 | 18.40% | +3.34 | -8.94% | n/a | 0.6% | 0.0% | 3.54 |
| CAP_DEF | 12.11% | +1.68 | 1.181 | +0.332 | 1.070 | +0.310 | 13.05% | -2.02 | -2.78% | n/a | 6.8% | 6.1% | 3.41 |

## Consistency of improvement vs BASE

Sign of delta across all 6 cells (train/validation x trend 36/40/44), same cadence. 'consistent+' = better in all 6, 'consistent-' = worse in all 6, else mixed (n+ of 6 better).

| variant | cadence | CAGR | Sharpe | xSharpe | MDD (lower is better) |
|---|---|---|---|---|---|
| CASHSLOT | W | mixed (4/6) | mixed (4/6) | mixed (4/6) | mixed (3/6) |
| CASHSLOT | M | mixed (4/6) | mixed (4/6) | mixed (4/6) | mixed (5/6) |
| DEF | W | mixed (4/6) | mixed (5/6) | mixed (4/6) | mixed (4/6) |
| DEF | M | mixed (4/6) | mixed (4/6) | mixed (4/6) | mixed (5/6) |
| CAP | W | mixed (3/6) | mixed (3/6) | mixed (3/6) | mixed (1/6) |
| CAP | M | mixed (2/6) | mixed (3/6) | mixed (3/6) | consistent- |
| CAP_DEF | W | mixed (3/6) | mixed (4/6) | mixed (4/6) | consistent+ |
| CAP_DEF | M | mixed (3/6) | mixed (4/6) | mixed (4/6) | consistent+ |
| CAND* | W | mixed (4/6) | consistent+ | consistent+ | mixed (3/6) |
| CAND_CAP_DEF* | W | mixed (1/6) | mixed (5/6) | mixed (5/6) | consistent+ |
