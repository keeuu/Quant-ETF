# CN validation summary (protocol_cn_validation.json, sha256 verified at run time)

| check | result | threshold / note |
|---|---|---|
| A1 price adjustment | NOT RUN as written (EM_HFQ unreachable); substitute PASS | per symbol abs(ann diff) <= 0.10 pp and no unexplained >50 bp day; EM_HFQ unreachable |
| A2 NAV tracking | PASS | abs(ann diff) <= 0.50 pp per symbol |
| A3 corporate actions | REPORTED (no pass threshold in protocol) | Sina hfq.js vs Eastmoney dividend/split records |
| A4 suspensions | EM part NOT RUN (unreachable); independent source: Tencent | EM unadjusted bars unreachable; Tencent/Sina/EM-NAV status reported |
| Data-corrected rerun | NOT TRIGGERED | triggered only if A1 or A2 fails |
| B1 neighbourhood | PASS (9/9) | >= 7 of 9 |
| B2 execution lag | PASS | all 5 offsets |
| B3 ETF substitution | PASS | every runnable variant |
| B4 small account (100k) | PASS | CAGR drop <= 1.0 pp and per-cell check |
| B5 2015 drawdown | descriptive | no threshold |

## Deviations

- Eastmoney bar endpoints (ak.fund_etf_hist_em hfq and unadjusted, push2his.eastmoney.com) were unreachable (3 tries each, all 6 symbols, logged in validation/data/fetch_log.json). A1 as written and the EM part of A4 are therefore NOT RUN. Closest faithful versions: A1 against an independent reconstruction (Tencent unadjusted bars + Eastmoney distribution/split records through the baseline adjustment code); A4 against Tencent bars, Sina bars and Eastmoney NAV status. The A1 reference shares the adjustment code with the baseline, so it tests prices and event records, not the algorithm; A2 (NAV total return) is independent of it.
- A3 uses the Eastmoney distribution/split tables behind ak.fund_fh_em / ak.fund_cf_em (queried directly with the same URL/params, JSON-parsed instead of eval).
- A2 NAV total return is compounded from Eastmoney's daily growth rate, which is rounded to 0.01%.
- All Part B cells use the baseline score_start (2014-09-01) so every cell shares one sample; benchmarks for B2 are lagged by the same k (unlagged-benchmark outcome also reported); B3 passes against the baseline benchmarks (substituted-CSI300 benchmark outcome also reported); B4 passes against benchmarks run under the same account rules (fractional-benchmark outcome also reported).
- B4 commission schedule is exactly as written (2.5 bp, min 5 CNY, plus 5 bp slippage), which is cheaper than the baseline 5+5 bps for large trades; a non-protocol sensitivity with 5 bp commission is shown.
- B4 lot executor: targets floor(w * V_pre / (open * 100)) lots on the pre-trade value; if fees would make cash negative, lots are removed from the largest buy one at a time. Every non-zero lot change is a trade (minimum fee applies).

