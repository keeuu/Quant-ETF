# Part A: data verification (protocol_cn_validation.json)

## Source reachability (data/fetch_log.json)

| source | status |
|---|---|
| EM_HFQ:510300 | UNREACHABLE |
| EM_UNADJ:510300 | UNREACHABLE |
| EM_HFQ:510050 | UNREACHABLE |
| EM_UNADJ:510050 | UNREACHABLE |
| EM_HFQ:510500 | UNREACHABLE |
| EM_UNADJ:510500 | UNREACHABLE |
| EM_HFQ:159915 | UNREACHABLE |
| EM_UNADJ:159915 | UNREACHABLE |
| EM_HFQ:518880 | UNREACHABLE |
| EM_UNADJ:518880 | UNREACHABLE |
| EM_HFQ:511010 | UNREACHABLE |
| EM_UNADJ:511010 | UNREACHABLE |
| EM_NAV:510300 | OK |
| EM_NAV:510050 | OK |
| EM_NAV:510500 | OK |
| EM_NAV:159915 | OK |
| EM_NAV:518880 | OK |
| EM_NAV:511010 | OK |
| EM_DIV | OK |
| EM_SPLIT | OK |
| TENCENT_UNADJ:510300 | OK |
| TENCENT_UNADJ:510050 | OK |
| TENCENT_UNADJ:510500 | OK |
| TENCENT_UNADJ:159915 | OK |
| TENCENT_UNADJ:518880 | OK |
| TENCENT_UNADJ:511010 | OK |
| SINA:510330 | OK first=2013-01-16 last=2026-10-16 rows=3337 |
| SINA:159922 | OK first=2013-03-15 last=2026-10-16 rows=3300 |

EM_HFQ reachable: **False**; EM unadjusted reachable: **False**. Per protocol `if_A_unreachable`, checks that need an unreachable source are reported NOT RUN; invented data is never substituted. Where a closest-faithful check from a reachable independent source exists it is shown and labelled as a substitute.

## A1: price adjustment (SUBSTITUTE reference; A1 as written NOT RUN)

Reference (when EM_HFQ is unreachable): Tencent unadjusted bars + Eastmoney distribution/split records, adjusted by the baseline adjustment code. Differences therefore test the Sina price source and the Sina hfq.js distribution/conversion records against two independent sources (Tencent, Eastmoney). Annualisation: compound return over consecutive common sessions / calendar years (365.25 d). Day is 'explained' if a Sina or EM event date lies within 3 common sessions.

| symbol | days | mean abs diff (bp) | max abs diff (bp) @ date | days >5bp | days >50bp (unexplained) | ann ours | ann ref | diff (pp) | PASS |
|---|---|---|---|---|---|---|---|---|---|
| 510300 | 3204 | 0.01 | 5.4 @ 2015-12-07 | 2 | 0 (0) | 7.175% | 7.175% | -0.000 | PASS |
| 510050 | 3204 | 0.02 | 15.8 @ 2018-08-31 | 2 | 0 (0) | 6.971% | 6.971% | -0.000 | PASS |
| 510500 | 3202 | 0.00 | 2.6 @ 2015-12-03 | 0 | 0 (0) | 7.654% | 7.654% | -0.000 | PASS |
| 159915 | 3203 | 0.00 | 0.0 @ 2013-07-30 | 0 | 0 (0) | 8.148% | 8.148% | +0.000 | PASS |
| 518880 | 3204 | 0.00 | 0.0 @ 2013-07-30 | 0 | 0 (0) | 9.456% | 9.456% | +0.000 | PASS |
| 511010 | 3204 | 0.01 | 10.8 @ 2018-08-20 | 2 | 0 (0) | 3.000% | 3.000% | +0.000 | PASS |

Reconstruction notes:
- 510300: EM event 2012-12-18 is at/before the first Tencent bar 2013-07-01; ignored
- 510300: EM event 2012-05-11 is at/before the first Tencent bar 2013-07-01; ignored
- 510050: EM event 2012-05-16 is at/before the first Tencent bar 2013-07-01; ignored
- 510050: EM event 2012-11-13 is at/before the first Tencent bar 2013-07-01; ignored
- 510500: EM event date 2015-04-14 has no Tencent bar; moved to 2015-04-15
- 518880: EM event 2013-07-24 is at/before the first Tencent bar 2013-07-29; ignored
- 511010: EM event 2013-06-03 is at/before the first Tencent bar 2013-07-01; ignored
- 511010: EM event 2013-03-05 is at/before the first Tencent bar 2013-07-01; ignored

Raw (unadjusted) close agreement, Sina vs Tencent:

| symbol | common days | days differing >0.1% | max rel diff | days in Tencent not Sina | days in Sina not Tencent |
|---|---|---|---|---|---|
| 510300 | 3205 | 0 | 0.00054 | 0 | 0 |
| 510050 | 3205 | 1 | 0.00119 | 0 | 0 |
| 510500 | 3203 | 0 | 0.00026 | 0 | 0 |
| 159915 | 3204 | 0 | 0.00000 | 0 | 0 |
| 518880 | 3205 | 0 | 0.00000 | 0 | 0 |
| 511010 | 3205 | 1 | 0.00107 | 0 | 0 |

## A2: NAV tracking (EM_NAV daily growth, 2-decimal percent)

| symbol | days | ann ours | ann NAV TR | diff (pp) | corr daily | max abs diff (bp) @ date | PASS (<=0.50pp) |
|---|---|---|---|---|---|---|---|
| 510300 | 3204 | 7.175% | 7.181% | -0.006 | 0.9802 | 460 @ 2015-07-10 | PASS |
| 510050 | 3204 | 6.971% | 6.976% | -0.004 | 0.9926 | 214 @ 2020-07-07 | PASS |
| 510500 | 3202 | 7.654% | 7.660% | -0.006 | 0.9760 | 759 @ 2015-07-08 | PASS |
| 159915 | 3203 | 8.148% | 8.145% | +0.003 | 0.9794 | 1001 @ 2015-07-08 | PASS |
| 518880 | 3204 | 9.456% | 9.399% | +0.057 | 0.9619 | 398 @ 2023-10-10 | PASS |
| 511010 | 3204 | 3.000% | 2.989% | +0.012 | 0.5897 | 130 @ 2020-02-04 | PASS |

## A3: corporate actions, Sina hfq.js (baseline) vs Eastmoney records (within 2013-07-29..2026-09-30)

EM_DIV = fund.eastmoney.com Data/funddataIndex_Interface.aspx dt=8 (same endpoint as `ak.fund_fh_em`); EM_SPLIT = dt=9 (`ak.fund_cf_em`). Match = amount equal (|diff|<=5e-4), date offset reported in sessions (<=3 allowed).

| symbol | kind | exact | date-offset | only in Sina | only in EM |
|---|---|---|---|---|---|
| 510300 | distribution | 13 | 0 | 0 | 0 |
| 510300 | conversion | 0 | 0 | 0 | 0 |
| 510050 | distribution | 12 | 0 | 0 | 0 |
| 510050 | conversion | 0 | 0 | 0 | 0 |
| 510500 | distribution | 4 | 0 | 0 | 0 |
| 510500 | conversion | 2 | 0 | 0 | 0 |
| 159915 | distribution | 0 | 0 | 0 | 0 |
| 159915 | conversion | 0 | 0 | 0 | 0 |
| 518880 | distribution | 0 | 0 | 0 | 0 |
| 518880 | conversion | 0 | 0 | 0 | 0 |
| 511010 | distribution | 6 | 0 | 0 | 0 |
| 511010 | conversion | 0 | 0 | 0 | 0 |

## A4: suspensions

|   symbol | session    | EM_unadjusted_bar                     | tencent_unadjusted_bar_exists   | sina_bar_exists   | EM_NAV_row_exists   |   EM_NAV_unit | EM_NAV_status   |
|---------:|:-----------|:--------------------------------------|:--------------------------------|:------------------|:--------------------|--------------:|:----------------|
|   510500 | 2015-04-13 | NOT RUN (EM bar endpoint unreachable) | False                           | False             | True                |        2.277  | 暂停申购/暂停赎回       |
|   510500 | 2015-04-14 | NOT RUN (EM bar endpoint unreachable) | False                           | False             | True                |        8.1198 | 暂停申购/暂停赎回       |
|   159915 | 2021-02-08 | NOT RUN (EM bar endpoint unreachable) | False                           | False             | True                |        3.1649 | 暂停交易/暂停交易       |

## Verdicts

- A1: NOT RUN as written (EM_HFQ unreachable); substitute PASS
- A2: PASS
- A3: REPORTED (no pass threshold in protocol)
- A4: EM part NOT RUN (unreachable); independent source: Tencent

Data-corrected rerun triggered by Part A: **False**
