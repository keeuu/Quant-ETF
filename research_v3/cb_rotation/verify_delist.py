"""Verifies the delisting classification (classify.py) against cninfo announcements.

For every bond whose last bar is on or before DEV_END (these are the ones that can hold a position through a terminal
event during development) plus a fixed sample of later-delisted bonds, fetches the underlying stock's cninfo
announcements around the last bar (keyword '转债'), keeps those naming the bond, and classifies the titles by keywords:
  redemption  : 赎回 (not 不提前赎回 / 不行使) ...
  maturity    : 到期兑付 / 到期赎回 / 本息兑付 / 兑付
  passive     : 未能兑付 / 违约 / 无法按期 / 终止上市 / 退市整理 / 摘牌 with default wording
  balance     : 余额不足 / 转股比例 (conversion exhausted)
Raw announcement lists are cached in data/cninfo/{code}.csv (>= 1 s between calls, 3 retries). Output:
artifacts_dev/delist_verification.csv. Run: python -m research_v3.cb_rotation.verify_delist
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import pandas as pd

from . import data as cbdata
from .classify import classify_all
from .fetch import FetchError, call

DATA = cbdata.DATA_DIR / "cninfo"
OUT = Path(__file__).resolve().parent / "artifacts_dev"
log = logging.getLogger("cb_rotation_fetch")

KW = {
    "passive": r"重整计划|破产重整|申请.{0,4}重整|未能.{0,6}兑付|无法.{0,6}兑付|不能.{0,6}兑付|违约|无法按期|未按期|终止上市|退市整理|强制退市|风险提示.{0,20}(兑付|偿付)",
    "maturity": r"到期兑付|到期赎回|本息兑付|兑付.{0,6}摘牌|到期摘牌|付息兑付|兑付公告|到期还本",
    "redemption": r"提前赎回|赎回暨摘牌|实施.{0,4}赎回|赎回结果|赎回的公告",
    "balance": r"余额不足|转股比例|低于3,?000万|3,?000万元|回售结果|回售的.{0,6}公告|回售实施",
}
NEG = r"不提前赎回|不行使.{0,6}赎回|不赎回"


def clean(t: str) -> str:
    return re.sub(r"<.*?>", "", str(t))


def fetch_cninfo(row) -> pd.DataFrame | None:
    import akshare as ak
    p = DATA / f"{row.code}.csv"
    if p.exists():
        return pd.read_csv(p, dtype=str)
    lb = pd.Timestamp(row.last_bar)
    s, e = (lb - pd.Timedelta(days=240)).strftime("%Y%m%d"), (lb + pd.Timedelta(days=150)).strftime("%Y%m%d")
    kw = "转债" if str(row.name).endswith(("转债", "转退")) else str(row.name)
    df = call(ak.stock_zh_a_disclosure_report_cninfo, symbol=row.stock, market="沪深京", keyword=kw,
              category="", start_date=s, end_date=e)
    if df is None or not len(df):          # titles of some bonds (e.g. 东财转2/3) do not contain the keyword: retry with 赎回
        df = call(ak.stock_zh_a_disclosure_report_cninfo, symbol=row.stock, market="沪深京", keyword="赎回",
                  category="", start_date=s, end_date=e)
    if df is None or not len(df):
        df = pd.DataFrame(columns=["公告时间", "公告标题"])
    df = df[["公告时间", "公告标题"]].copy()
    df["公告标题"] = df["公告标题"].map(clean)
    df.to_csv(p, index=False)
    return df


def evidence(df: pd.DataFrame, name: str, last_bar: str) -> dict:
    """Keyword evidence from the stock's convertible-bond announcements in the fetch window (titles often omit the bond
    name, so no name filter is applied; multi-bond issuers are caught by the manual review of disagreements)."""
    d = df
    ev = {k: [] for k in KW}
    for dt, t in zip(d["公告时间"], d["公告标题"]):
        if re.search(NEG, t):
            continue
        if re.search(KW["passive"], t):
            ev["passive"].append(f"{str(dt)[:10]} {t}")
            continue
        for k in ("maturity", "redemption", "balance"):
            if re.search(KW[k], t):
                ev[k].append(f"{str(dt)[:10]} {t}")
    ev["first_redeem_date"] = min((x[:10] for x in ev["redemption"]), default="")
    return ev


def combine(ev: dict) -> str:
    """Success announcements (redemption result / maturity payment) beat risk warnings; then default wording; then
    put/balance-exhausted wording."""
    strong_red = [x for x in ev["redemption"] if re.search(r"赎回结果|赎回暨摘牌|实施.{0,6}赎回|提前赎回", x)]
    if ev["maturity"] and not strong_red:
        return "maturity"
    if strong_red:
        return "redemption"
    if ev["passive"]:
        return "passive"
    if ev["maturity"]:
        return "maturity"
    if ev["balance"]:
        return "balance"
    return "none"


def main(sample_later: int = 6):
    from .fetch import LOG
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG), logging.StreamHandler()])
    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(exist_ok=True)
    lst = cbdata.read_list()
    meta = cbdata.bond_meta(lst)
    cl = classify_all(lst, meta)
    dev = cl[cl["last_bar"] <= "2023-12-31"]
    later = cl[cl["last_bar"] > "2023-12-31"]
    smp = pd.concat([g.sort_values("code").iloc[::max(1, len(g) // sample_later)].head(sample_later)
                     for _, g in later.groupby("rule_type")] + [later[later["reason_sh"] == "6"].head(sample_later)])
    todo = pd.concat([dev, smp]).drop_duplicates("code")
    rows = []
    for r in todo.itertuples():
        try:
            df = fetch_cninfo(r)
        except FetchError as e:
            log.warning("cninfo %s failed: %s", r.code, e)
            continue
        ev = evidence(df, r.name, r.last_bar)
        rows.append(dict(code=r.code, name=r.name, stock=r.stock, last_bar=r.last_bar, dev_period=r.last_bar <= "2023-12-31",
                         rating=r.rating, reason_sh=r.reason_sh, reason_hs=r.reason_hs, notice=r.notice, maturity=r.maturity,
                         rule_type=r.rule_type, rule=r.rule, cninfo_type=combine(ev),
                         n_titles=len(df), passive_titles=" || ".join(ev["passive"][:3]),
                         maturity_titles=" || ".join(ev["maturity"][:2]), redemption_titles=" || ".join(ev["redemption"][:2]),
                         balance_titles=" || ".join(ev["balance"][:2]),
                         first_redeem_date=ev["first_redeem_date"]))
    out = pd.DataFrame(rows)
    nonp = lambda s: s.map(lambda x: "passive" if x == "passive" else ("none" if x in ("none", "balance") else "non_passive"))
    # bonds whose last bar is after the development window: classification evidence only, no post-window dates kept
    post = ~out["dev_period"]
    for c in ("passive_titles", "maturity_titles", "redemption_titles", "balance_titles", "first_redeem_date"):
        out.loc[post, c] = ""
    for c in ("last_bar", "notice", "maturity"):
        out[c] = out[c].astype(object)
        out.loc[post, c] = ">2023-12-31" if c == "last_bar" else ""
    out["agree"] = nonp(out["rule_type"]) == nonp(out["cninfo_type"])
    out.to_csv(OUT / "delist_verification.csv", index=False)
    log.info("verification rows %d, agree %d", len(out), int(out["agree"].sum()))
    print(pd.crosstab(out["rule_type"], out["cninfo_type"]))


if __name__ == "__main__":
    main()


def summary_text(ver: pd.DataFrame, overrides: dict) -> str:
    """Chinese paragraph(s) + sample table summarising delist_verification.csv."""
    from .summary import md_table
    v = ver.copy()
    n, ag = len(v), int(v["agree"].sum())
    L = [f"对 {n} 只债券（开发期内停止交易的全部 {int(v['dev_period'].sum())} 只，加每类各抽样的开发期之后退市的 {int((~v['dev_period']).sum())} 只，含全部可查到的 EXECUTE_REASON_SH=6）"
         f"逐只用巨潮公告标题（正股代码 + 关键词“转债”，最后交易日前 240 天至后 150 天）做关键词判型，与规则分类比较（按是否被动区分）：一致 {ag} 只，不一致 {n - ag} 只。"]
    x = pd.crosstab(v["rule_type"], v["cninfo_type"])
    L.append("")
    L.append("规则分类（行）× 巨潮判型（列）：")
    L.append("")
    L.append(md_table(x.reset_index().rename(columns={"rule_type": "规则\\巨潮"})))
    if overrides:
        L.append("")
        L.append(f"人工逐只核对后修正 {len(overrides)} 只（见 delist_overrides.json）：" + "；".join(
            f"{k} → {o['type']}（{o['evidence'][:60]}）" for k, o in overrides.items()))
    s6 = v[v["reason_sh"].astype(str) == "6"]
    if len(s6):
        L.append("")
        L.append(f"EXECUTE_REASON_SH=6 共核对 {len(s6)} 只：规则分类 " + "、".join(f"{k} {c} 只" for k, c in s6["rule_type"].value_counts().items())
                 + "；巨潮判型 " + "、".join(f"{k} {c} 只" for k, c in s6["cninfo_type"].value_counts().items()) + "。")
    pick = []
    for typ in ("redemption", "maturity", "passive"):
        g = v[(v["rule_type"] == typ) & v["dev_period"]].sort_values("code")
        pick.append(g.iloc[::max(1, len(g) // 3)].head(3))
    pick.append(s6)
    pick.append(v[~v["agree"]])
    smp = pd.concat(pick).drop_duplicates("code")
    ev_col = lambda r: next((str(r[c])[:70] for c in ("passive_titles", "maturity_titles", "redemption_titles", "balance_titles")
                             if isinstance(r[c], str) and r[c]), "(无匹配标题)")
    smp = pd.DataFrame({"代码": smp["code"], "名称": smp["name"], "最后交易日": smp["last_bar"], "EXECUTE_REASON_SH": smp["reason_sh"].fillna("-"),
                        "规则": smp["rule_type"], "巨潮": smp["cninfo_type"], "巨潮标题示例": [ev_col(r) for _, r in smp.iterrows()]})
    L.append("")
    L.append("各类抽样（每类 3 只）、全部 SH=6 与全部不一致的债券：")
    L.append("")
    L.append(md_table(smp))
    red = v[v["dev_period"] & (v["rule_type"] == "redemption") & v["first_redeem_date"].notna() & (v["first_redeem_date"] != "") & v["notice"].notna()].copy()
    if len(red):
        gap = (pd.to_datetime(red["notice"]) - pd.to_datetime(red["first_redeem_date"])).dt.days
        L.append("")
        L.append(f"东财赎回公告日（NOTICE_DATE）与巨潮最早赎回类公告日之差（东财 − 巨潮，天；{len(red)} 只赎回类债券）：中位 {gap.median():.0f}，"
                 f"|差| > 10 天的 {int((gap.abs() > 10).sum())} 只，东财晚于巨潮 > 10 天的 {int((gap > 10).sum())} 只，东财早于巨潮 > 10 天的 {int((gap < -10).sum())} 只。")
    return "\n".join(L)
