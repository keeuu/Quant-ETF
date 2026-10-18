# research_v3 step 2: signal-variant comparison

Settings: V3_DEFAULT (^IRX cash, 5 bps cost + 5 bps slippage). Train and validation only. Means across trend lengths 36/40/44. Deltas vs BASE of the same cadence/partition. Sharpe rf=0; xSharpe = Sharpe of returns minus IRX daily accrual.

## train / W

| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | turnover/yr | cash wt |
|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 7.07% | +0.00 | 0.535 | +0.000 | 0.462 | +0.000 | 20.89% | +0.00 | 6.18 | 0.1% |
| ENS | 7.64% | +0.57 | 0.567 | +0.032 | 0.494 | +0.032 | 21.28% | +0.39 | 6.47 | 0.1% |
| BUF | 6.77% | -0.30 | 0.515 | -0.020 | 0.442 | -0.020 | 21.32% | +0.43 | 4.09 | 0.1% |
| IVOL | 7.00% | -0.07 | 0.558 | +0.023 | 0.479 | +0.018 | 19.92% | -0.97 | 7.08 | 0.1% |
| COMBO | 6.99% | -0.08 | 0.548 | +0.012 | 0.471 | +0.009 | 19.96% | -0.93 | 4.44 | 0.1% |

## train / M

| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | turnover/yr | cash wt |
|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 9.22% | +0.00 | 0.649 | +0.000 | 0.578 | +0.000 | 25.65% | +0.00 | 2.50 | 0.0% |
| ENS | 9.27% | +0.05 | 0.648 | -0.000 | 0.578 | +0.000 | 25.17% | -0.49 | 2.65 | 0.0% |
| BUF | 8.22% | -1.00 | 0.587 | -0.062 | 0.517 | -0.061 | 33.00% | +7.34 | 1.67 | 0.0% |
| IVOL | 9.80% | +0.59 | 0.721 | +0.072 | 0.646 | +0.067 | 21.33% | -4.33 | 2.78 | 0.0% |
| TRANCHE | 8.46% | -0.75 | 0.626 | -0.023 | 0.552 | -0.027 | 25.34% | -0.31 | 2.69 | 0.4% |
| COMBO | 9.17% | -0.05 | 0.670 | +0.021 | 0.596 | +0.017 | 21.53% | -4.13 | 2.23 | 0.0% |
| COMBO+TRANCHE | 8.31% | -0.91 | 0.644 | -0.005 | 0.565 | -0.013 | 19.66% | -6.00 | 2.40 | 0.4% |

## validation / W

| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | turnover/yr | cash wt |
|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 8.54% | +0.00 | 0.726 | +0.000 | 0.635 | +0.000 | 16.00% | +0.00 | 6.91 | 0.5% |
| ENS | 11.00% | +2.45 | 0.873 | +0.147 | 0.785 | +0.151 | 20.91% | +4.91 | 7.01 | 0.5% |
| BUF | 9.05% | +0.50 | 0.762 | +0.036 | 0.671 | +0.036 | 18.22% | +2.22 | 4.24 | 0.5% |
| IVOL | 6.84% | -1.70 | 0.634 | -0.092 | 0.535 | -0.099 | 15.38% | -0.62 | 8.03 | 0.5% |
| COMBO | 10.67% | +2.12 | 0.923 | +0.197 | 0.827 | +0.192 | 16.59% | +0.59 | 4.91 | 0.5% |

## validation / M

| variant | CAGR | dCAGR pp | Sharpe | dSharpe | xSharpe | dxSharpe | MDD | dMDD pp | turnover/yr | cash wt |
|---|---|---|---|---|---|---|---|---|---|---|
| BASE | 10.43% | +0.00 | 0.849 | +0.000 | 0.760 | +0.000 | 15.07% | +0.00 | 3.27 | 0.6% |
| ENS | 10.12% | -0.31 | 0.807 | -0.042 | 0.720 | -0.040 | 21.23% | +6.16 | 3.30 | 0.6% |
| BUF | 9.00% | -1.44 | 0.749 | -0.100 | 0.659 | -0.100 | 15.07% | -0.00 | 2.34 | 0.6% |
| IVOL | 10.05% | -0.38 | 0.863 | +0.014 | 0.768 | +0.009 | 15.29% | +0.22 | 3.81 | 0.6% |
| TRANCHE | 10.85% | +0.41 | 0.906 | +0.057 | 0.814 | +0.054 | 17.87% | +2.81 | 3.06 | 1.5% |
| COMBO | 9.28% | -1.15 | 0.804 | -0.045 | 0.709 | -0.050 | 16.43% | +1.36 | 2.94 | 0.6% |
| COMBO+TRANCHE | 9.77% | -0.66 | 0.834 | -0.015 | 0.740 | -0.020 | 22.84% | +7.77 | 2.65 | 1.5% |

## Consistency of improvement vs BASE

Sign of delta across all 6 cells (train/validation x trend 36/40/44), same cadence. 'consistent+' = better in all 6, 'consistent-' = worse in all 6, else mixed (n+ of 6 better).

| variant | cadence | CAGR | Sharpe | xSharpe | MDD (lower is better) |
|---|---|---|---|---|---|
| ENS | W | consistent+ | consistent+ | consistent+ | mixed (1/6) |
| ENS | M | mixed (1/6) | mixed (1/6) | mixed (1/6) | mixed (3/6) |
| BUF | W | mixed (2/6) | mixed (2/6) | mixed (2/6) | consistent- |
| BUF | M | consistent- | consistent- | consistent- | mixed (1/6) |
| IVOL | W | mixed (1/6) | mixed (3/6) | mixed (3/6) | consistent+ |
| IVOL | M | mixed (3/6) | mixed (5/6) | mixed (5/6) | mixed (4/6) |
| TRANCHE | M | mixed (3/6) | mixed (3/6) | mixed (3/6) | mixed (1/6) |
| COMBO | W | mixed (5/6) | mixed (5/6) | mixed (5/6) | mixed (3/6) |
| COMBO | M | mixed (2/6) | mixed (2/6) | mixed (2/6) | mixed (3/6) |
| COMBO+TRANCHE | M | mixed (1/6) | mixed (3/6) | mixed (3/6) | mixed (3/6) |
