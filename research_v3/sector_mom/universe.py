"""Step 1b: keyword classification of SH/SZ ETFs by tracked-index name (written and run BEFORE any price download).

Inputs (universe/): raw_ths_etf_list.json, raw_akshare_only_candidates.json, raw_profiles.jsonl (fund_overview_em).
Output (universe/): classified_universe.csv (every candidate, included and excluded, with reason) + .sha256.

Classification text: tracked index (跟踪标的) when informative; else the benchmark text (业绩比较基准); else the fund
short name. Rules are applied in this fixed order; the first exclusion that fires wins; an ETF is INCLUDED only if no
exclusion fires AND at least one industry/theme keyword matches.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
UDIR = HERE / "universe"
OUT_CSV = UDIR / "classified_universe.csv"
OUT_HASH = UDIR / "classified_universe.sha256"

# ---- exclusion rules (order matters) -------------------------------------------------------------------------
FUND_TYPE_EXCL = [("money_market", r"货币"), ("bond", r"债券"), ("qdii", r"QDII"), ("commodity", r"商品"),
                  ("reits", r"REIT")]
BOND_MONEY_COMMODITY = [
    ("bond", r"债|利率|信用|国开|地方政府|可转"),
    ("money_market", r"货币|现金管理|日利|添益|快线|保证金|交易型货币"),
    ("gold_commodity", r"黄金|金银|白银|原油|豆粕|大宗商品|商品|期货|能源化工"),
]
CROSS_BORDER = r"港股|恒生|香港|港元|港币|沪港深|沪深港|AH|中概|海外|美国|纳斯达克|纳指|标普|道琼斯|道指|日经|东证|德国|法国|DAX|CAC|巴西|BOVESPA|越南|沙特|印度|东南亚|亚太|全球|欧洲|英国|富时|MSCI|罗素|韩国|台湾|中韩|日本|印尼|新兴市场|金砖|一带一路|石油天然气.*美|Nasdaq|S&P|NASDAQ|纽约|安硕"
# broad / size / board indices: matched on the normalised index name (index text minus spaces)
BROAD = [r"^上证(50|100|180|380|综合|指数|龙头)?(指数)?$", r"^上证(180|380|50)(指数)?$", r"^上证(红利|国企|央企)", r"^沪深300(指数)?$",
         r"^中证(100|200|300|500|800|1000|2000|A50|A100|A500|A股|全指|流通|规模|大盘|中盘|小盘)(指数)?$",
         r"^中证(500|800|1000|2000)(指数)?$", r"^中证A(50|100|500)", r"^中证A股", r"^中证全指(指数)?$", r"^中证(1000|2000)",
         r"^深证(50|100|300|成指|综合|成份)", r"^深证(成份|综指)", r"^深成", r"^深圳(成指)",
         r"^创业板(指|指数|50|100|综合|综指|成长|价值|中盘|大盘|小盘|低波|动量成长|Ex|EX)",
         r"^创业板$", r"^创业板(指数)?$", r"^创业板.?(综合|综指)", r"^创业板.*(成长|价值|低波|动量|质量|红利)",
         r"^科创(50|100|200|综指|综合|创业50|成长|价值|板50|板100|板200|板综合|板综指|板成长)", r"^科创板(50|100|200|综|成长)",
         r"^中证科创创业50", r"^上证科创板(50|100|200)", r"^科创创业50",
         r"^国证(2000|1000|A指|大盘|中盘|小盘|成长|价值|[0-9]+价值|[0-9]+成长|央企|红利|治理|ESG|[0-9]+)(指数)?$",
         r"^国证(大盘|中盘|小盘|成长|价值|治理|基本面|红利|自由现金流|ESG|低波|2000|1000|A指)",
         r"^国证价值|^国证成长",
         r"^北证50", r"^MSCI", r"^富时"]
STYLE = r"红利|股息|高息|价值|成长|低波|低波动|波动率|质量|基本面|动量|ESG|责任|治理|等权|自由现金流|现金流|回购|央企|国企|民企|国有|绩优|量化|增强|因子|低碳(?!经济)|大盘|中盘|小盘|中小盘|专精特新|创新驱动|龙头50|百强|策略|行业中性"
# ---- positive industry / thematic keywords -------------------------------------------------------------------
INDUSTRY = r"""半导体|芯片|集成电路|电子|通信|5G|计算机|软件|云计算|大数据|人工智能|AI|机器人|信息技术|信息安全|网络安全|信创|数字|数据|互联网|物联网|工业互联网|区块链|元宇宙|算力|传媒|游戏|动漫|影视|文化|娱乐|体育|旅游|免税|酒店|餐饮|卫星|航天|航空|通用航空|军工|国防|国安|证券|券商|银行|保险|金融|非银|地产|房地产|基建|建筑|建材|钢铁|煤炭|有色|稀土|稀有金属|黄金股|金属|化工|石化|石油|油气|能源|电力|公用事业|电网|新能源|光伏|风电|电池|储能|锂|新能车|新能源车|汽车|智能驾驶|医药|医疗|医疗器械|医疗保健|生物|创新药|中药|疫苗|CXO|基因|健康|养老|消费|食品|饮料|白酒|酒|家电|家居|农业|农牧|畜牧|养殖|粮食|种业|猪|纺织|轻工|造纸|环保|环境|碳中和|绿色|低碳|交运|交通|物流|运输|航运|港口|铁路|机械|工程机械|装备|制造|氢能|核电|新材料|材料|工业|科技|教育|商贸|零售|电商|家用电器|机床|虚拟现实|VR|车联网|智能网联|电信|船舶|制药|药|资源"""
INDUSTRY = INDUSTRY.replace("|红利", "").replace("\n", "")


def norm_index(name: str) -> str:
    s = re.sub(r"[\s　]", "", str(name or ""))
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\((人民币|价格|收益率|全收益|净收益|PR|TR)\)", "", s)
    s = re.sub(r"(收益率|指数)$", "", s)
    return s


def board_limit(text: str) -> float:
    """20% price-limit board proxy: tracked index text mentions ChiNext (创业板) or STAR (科创); else 10%."""
    return 0.20 if re.search(r"创业板|科创", text or "") else 0.10


def classify_row(rec: dict, prof: dict | None) -> dict:
    name = rec.get("name") or (prof or {}).get("基金简称") or ""
    ftype = (prof or {}).get("基金类型") or ""
    idx_raw = (prof or {}).get("跟踪标的") or ""
    bench = (prof or {}).get("业绩比较基准") or ""
    if prof is None or prof.get("error"):
        text, basis = name, "fund_name(no_profile)"
    elif idx_raw and "无跟踪标的" not in idx_raw and idx_raw not in ("---", "--"):
        text, basis = idx_raw, "tracked_index"
    elif bench and bench not in ("---", "--"):
        text, basis = bench, "benchmark"
    else:
        text, basis = name, "fund_name"
    nidx = norm_index(text)
    out = {"ticker": rec["ticker"], "thscode": rec["thscode"], "exchange": rec["exchange"], "name": name,
           "fund_type": ftype, "tracked_index_raw": idx_raw, "classification_basis": basis,
           "index_key": nidx if basis == "tracked_index" else (nidx if basis == "benchmark" else f"NAME:{name}"),
           "price_limit": board_limit(text), "source": rec.get("source", "ths_list"),
           "estab_date": (prof or {}).get("成立日期/规模", "") or ""}
    allt = f"{text}|{name}"
    def ex(reason): return {**out, "included": False, "reason": reason}
    if rec["exchange"] not in ("SH", "SZ"):
        return ex("not_SH_SZ")
    for r, pat in FUND_TYPE_EXCL:
        if re.search(pat, ftype):
            return ex(f"fund_type:{r}")
    if re.search(CROSS_BORDER, allt):
        return ex("cross_border_or_HK")
    for r, pat in BOND_MONEY_COMMODITY:
        if re.search(pat, allt):
            return ex(r)
    for pat in BROAD:
        if re.search(pat, nidx):
            return ex("broad_or_size_or_board_index")
    if re.search(STYLE, nidx if basis != "fund_name" else name):
        return ex("style_or_strategy_index")
    if not re.search(INDUSTRY, text):
        return ex("no_industry_or_theme_keyword")
    return {**out, "included": True, "reason": "industry_or_theme_index" if basis == "tracked_index"
            else f"industry_or_theme(by_{basis})"}


def build(udir: Path = UDIR) -> pd.DataFrame:
    ths = json.loads((udir / "raw_ths_etf_list.json").read_text())
    extra = json.loads((udir / "raw_akshare_only_candidates.json").read_text())
    profs = {}
    for ln in (udir / "raw_profiles.jsonl").read_text().splitlines():
        d = json.loads(ln)
        profs[d["ticker"]] = d
    rows = [classify_row(r, profs.get(r["ticker"])) for r in sorted(ths + extra, key=lambda x: x["ticker"])]
    return pd.DataFrame(rows)


def save(df: pd.DataFrame, udir: Path = UDIR) -> str:
    df.to_csv(udir / "classified_universe.csv", index=False)
    h = hashlib.sha256((udir / "classified_universe.csv").read_bytes()).hexdigest()
    (udir / "classified_universe.sha256").write_text(h + "\n")
    return h


def load_included(udir: Path = UDIR) -> pd.DataFrame:
    """Hash-verified; price-fetch and run code reads ONLY from here."""
    p = udir / "classified_universe.csv"
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if h != (udir / "classified_universe.sha256").read_text().strip():
        raise RuntimeError("classified_universe.csv hash mismatch")
    df = pd.read_csv(p, dtype={"ticker": str})
    return df[df.included].reset_index(drop=True)


if __name__ == "__main__":
    df = build()
    print(save(df))
    print(df.included.value_counts().to_dict())
    print(df.reason.value_counts().to_string())
