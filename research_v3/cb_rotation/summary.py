"""Writes artifacts_dev/summary.md (Chinese) from the run context."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

LABEL = {"SMALL_DEC": "SMALL 决策版 (1bp/最低1元, 滑点10bp, 被动退市0%)", "SMALL_MIN5": "SMALL 最低佣金5元",
         "SMALL_SLIP30": "SMALL 滑点30bp", "SMALL_PASS50": "SMALL 被动退市按50%回收", "LARGE": "LARGE 100万(可拆分)",
         "SMALL_EXITEVT": "SMALL 敏感性: 强制退出不含流动性条件", "EW_CB": "EW_CB 基准(全合格等权, LARGE)", "CB_INDEX": "CB_INDEX 中证转债(000832)价格指数",
         "CSI300": "CSI300 (510300 全收益)"}
ORDER = ["SMALL_DEC", "SMALL_MIN5", "SMALL_SLIP30", "SMALL_PASS50", "SMALL_EXITEVT", "LARGE", "EW_CB", "CB_INDEX", "CSI300"]


def pct(x, d=1):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def num(x, d=2):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_summary(ctx, out: Path):
    m, dec, boot, psr = ctx["metrics"], ctx["dec"], ctx["boot"], ctx["psr"]
    cl, dev_cl, uni, diag, p = ctx["cl"], ctx["dev_cl"], ctx["uni"], ctx["diag"], ctx["p"]
    meta, lst = ctx["meta"], ctx["lst"]
    ver = ctx.get("verification")
    ind = ctx.get("independent")
    g = lambda run, per: m[(m["run"] == run) & (m["period"] == per)].iloc[0]
    L = []
    A = L.append
    A("# 可转债双低轮动：开发阶段结果 (2018-2023)")
    A("")
    A("协议：`protocol_cb_rotation.json`（sha256 fe867343…，运行时已校验）。本文件只含开发期 2018-01-01 至 2023-12-31；2024 年起的留出期没有被读取，也没有任何留出期数字。")
    A("")
    A(f"## 一、结论：**{dec['VERDICT']}**")
    A("")
    rows = []
    names = {"c1_dev_xs_gt_EW_CB": "DEV 超额夏普 > EW_CB", "c1_dev_xs_gt_CB_INDEX": "DEV 超额夏普 > CB_INDEX",
             "c2_devA_xs_gt_EW_CB": "DEV_A 超额夏普 > EW_CB", "c2_devB_xs_gt_EW_CB": "DEV_B 超额夏普 > EW_CB",
             "c3_dev_mdd_le_25pct": "DEV 最大回撤 <= 25%", "c4_slip30_xs_gt_EW_CB": "滑点30bp: DEV 超额夏普 > EW_CB",
             "c4_slip30_xs_gt_CB_INDEX": "滑点30bp: DEV 超额夏普 > CB_INDEX",
             "c4_min5_xs_gt_EW_CB": "最低佣金5元: DEV 超额夏普 > EW_CB", "c4_min5_xs_gt_CB_INDEX": "最低佣金5元: DEV 超额夏普 > CB_INDEX"}
    for k, label in names.items():
        v = dec[k]
        fmt = pct if k.startswith("c3") else num
        rows.append({"条件": label, "策略值": fmt(v["value"]), "对照值": fmt(v["threshold_or_benchmark"]),
                     "通过": "是" if v["passed"] else "否"})
    A(md_table(pd.DataFrame(rows)))
    A("")
    if dec["VERDICT"] == "PASS":
        A("九项条件全部满足。按协议下一步是向用户申请一次性留出期运行；本次未运行留出期。")
    else:
        A("至少一项条件未满足，协议判定 FAIL：只报告，留出期不使用。")
    A("")
    A("## 二、开发期主表 (2018-01-01..2023-12-31)")
    A("")
    t = []
    for r in ORDER:
        x = g(r, "DEV")
        t.append({"版本": LABEL[r], "CAGR": pct(x["cagr"]), "年化波动": pct(x["ann_vol"]), "超额夏普": num(x["xsharpe"]),
                  "最大回撤": f"{pct(x['max_drawdown'])} ({x['mdd_peak']} → {x['mdd_trough']})",
                  "最差整年": f"{x['worst_year_label']}: {pct(x['worst_full_year'])}",
                  "换手(年, 单边)": pct(x.get("ann_one_way_turnover")) if "ann_one_way_turnover" in x and pd.notna(x.get("ann_one_way_turnover")) else "-",
                  "成本拖累(年)": pct(x.get("cost_drag_ann"), 2) if pd.notna(x.get("cost_drag_ann")) else "-",
                  "成交笔数": "-" if pd.isna(x.get("n_trades")) else int(x["n_trades"]),
                  "平均持仓": num(x.get("mean_holdings")), "平均现金": pct(x.get("mean_cash_weight")),
                  "强制退出": "-" if pd.isna(x.get("forced_exits")) else int(x["forced_exits"]),
                  "持有时被动退市": "-" if pd.isna(x.get("passive_delist_held")) else int(x["passive_delist_held"]),
                  "被阻挡订单": "-" if pd.isna(x.get("blocked_orders")) else int(x["blocked_orders"])})
    A(md_table(pd.DataFrame(t)))
    A("")
    held = [t for t in ctx["res"]["SMALL_DEC"].terminal if t["type"] == "passive"]
    if held:
        A("SMALL 决策版持有时发生被动退市：" + "；".join(f"{t['name']}（最后交易日 {t['date']}，最后收盘 {t['last_close']:.2f}，持仓 {t['qty']:.0f} 张）" for t in held)
          + f"，按 0% 计损失 {g('SMALL_DEC', 'DEV')['passive_loss_cny']:,.0f} 元（LARGE：{g('LARGE', 'DEV')['passive_loss_cny']:,.0f} 元；50% 敏感性版：SMALL {g('SMALL_PASS50', 'DEV')['passive_loss_cny']:,.0f} 元）。")
        A("")
    A("分段超额夏普（DEV_A = 2018-2020，DEV_B = 2021-2023）：")
    A("")
    t = []
    for r in ORDER:
        t.append({"版本": LABEL[r], "DEV": num(g(r, "DEV")["xsharpe"]), "DEV_A": num(g(r, "DEV_A")["xsharpe"]),
                  "DEV_B": num(g(r, "DEV_B")["xsharpe"]), "DEV_A CAGR": pct(g(r, "DEV_A")["cagr"]),
                  "DEV_B CAGR": pct(g(r, "DEV_B")["cagr"]), "DEV_A 最大回撤": pct(g(r, "DEV_A")["max_drawdown"]),
                  "DEV_B 最大回撤": pct(g(r, "DEV_B")["max_drawdown"])})
    A(md_table(pd.DataFrame(t)))
    A("")
    cy = ctx["cy"].pivot(index="year", columns="run", values="ret")[ORDER]
    A("日历年收益：")
    A("")
    cyt = cy.copy()
    cyt.columns = ORDER
    cyt = cyt.map(lambda v: pct(v)).reset_index().rename(columns={"year": "年份"})
    A(md_table(cyt))
    A("")
    A("## 三、Bootstrap 与 PSR")
    A("")
    A("平稳块自助法：块长 21，2000 次重采样，种子 20261019；超额夏普差（相对 SHIBOR 3M 现金）的 90% 区间。")
    A("")
    b = boot.copy()
    b["超额夏普差"] = b["xsharpe_diff"].map(num)
    b["90% 区间"] = [f"[{num(a)}, {num(c)}]" for a, c in zip(b["ci90_lo"], b["ci90_hi"])]
    b["区间不含0"] = b["ci_excludes_0"].map({True: "是", False: "否"})
    A(md_table(b[["comparison", "period", "n_days", "超额夏普差", "90% 区间", "区间不含0"]].rename(
        columns={"comparison": "比较", "period": "区间", "n_days": "天数"})))
    A("")
    A("PSR(0)（超额收益的夏普为正的概率）：")
    A("")
    pp = psr.pivot(index="run", columns="period", values="psr0").loc[ORDER, ["DEV", "DEV_A", "DEV_B"]].map(lambda v: num(v, 3))
    pp.index = [LABEL[i] for i in pp.index]
    A(md_table(pp.reset_index().rename(columns={"index": "版本"})))
    A("")
    A("## 四、数据")
    A("")
    ds = ctx["data_stats"]
    A(f"- 东财名单 {ds['n_list']} 行；其中 {ds['n_404']} 个 404xxx 退债代码（无行情，已跳过），已上市的正式债券 {ds['n_listed'] - ds['n_404']} 只：仍在市（无摘牌日）{ds['n_live']} 只，"
      f"有摘牌日 {ds['n_delisted_total']} 只（其中 2017-07-01 前摘牌、未下载 {ds['n_pre2017']} 只）。")
    A(f"- 已下载且最后一根日线早于 2026-10-01 的已停止交易债券 {ds['n_stopped']} 只，按分类：赎回 {ds['n_red']}、到期 {ds['n_mat']}、被动 {ds['n_pas']}。")
    A(f"- 下载：需下载 {ds['n_need']} 只（上市日期 <= 今日且未在 2017-07-01 前摘牌）；成功 {ds['n_ok']} 只三源齐全；失败 {ds['n_fail']} 项。")
    A(f"- 开发期面板：{p.close.shape[1]} 只债券 × {p.close.shape[0]} 个交易日 ({p.sessions[0]}..{p.sessions[-1]})；{diag['bars_total']} 根日线，"
      f"其中 {diag['amount_approx_bars']} 根腾讯成交额缺失，用 收盘价×成交量 近似（占 {diag['amount_approx_bars'] / diag['bars_total'] * 100:.2f}%）。")
    A(f"- 每周信号日合格债券数：最小 {int(uni['eligible'].min())}，中位 {int(uni['eligible'].median())}，最大 {int(uni['eligible'].max())}"
      f"（有评分的：{int(uni['eligible_with_score'].min())}–{int(uni['eligible_with_score'].max())}）。")
    A(f"- 行情日期不在股票日历内的日线：{diag.get('bars_off_calendar', 0)}；有行情但无溢价率的日线：{diag['bar_sessions_without_premium']}；"
      f"用 收盘价/转股价值-1 回填溢价率的日线：{diag['premium_fallback']}。")
    A("")
    A("## 五、被动退市分类核对")
    A("")
    A(ctx.get("verification_text", "（见 delist_verification.csv）"))
    A("")
    A("开发期（最后一根日线在 2018-01-01..2023-12-31）被动退市清单：")
    A("")
    pl = dev_cl[dev_cl["type"] == "passive"][["code", "name", "last_bar", "delist_date", "rating", "rule", "override", "held_by_SMALL_DEC", "held_by_LARGE"]]
    A(md_table(pl.rename(columns={"code": "代码", "name": "名称", "last_bar": "最后交易日", "delist_date": "摘牌日", "rating": "评级",
                                  "rule": "规则", "override": "核对修正", "held_by_SMALL_DEC": "SMALL持有", "held_by_LARGE": "LARGE持有"})))
    A("")
    A("## 六、独立复核")
    A("")
    if ind is not None:
        A(md_table(ind[["date", "identical_set", "identical_order", "n_eligible_engine", "n_eligible_independent", "max_abs_score_diff"]]))
        A("")
    A("## 七、偏差与声明")
    A("")
    for i, x in enumerate(ctx["deviations"], 1):
        A(f"{i}. {x}")
    A("")
    (out / "summary.md").write_text("\n".join(L))


def deviations(ctx) -> list:
    ds, diag, fe = ctx["data_stats"], ctx["diag"], ctx.get("forced_causes")
    cause = fe["cause"].value_counts().to_dict() if fe is not None and len(fe) else {}
    d = [
        "东财 `RPTA_WEB_KZZ_LS` 直接请求 15 列原始数据（akshare `bond_zh_cov_value_analysis` 列数不符）；溢价率直接取 `SWAPOR`，只在 `SWAPOR` 缺失而 `SWAPVALUE` 存在时用 收盘价/转股价值-1 回填，序列在最后一根日线处截断。转股价列（`SWAPPRICE`）只用于交叉检查，不进入评分；评分不需要转股价。",
        f"腾讯成交额缺失的日线（{diag['amount_approx_bars']} 根，约 {diag['amount_approx_bars'] / diag['bars_total'] * 100:.2f}%）用 收盘价×新浪成交量 近似（重叠日线上两者相差约 0.05%）。",
        "“上市满 10 个交易日”按已有日线根数计（含面板起点之前的日线）；“20 日均成交额”把无日线的交易日按 0 计，上市不足 20 日时分母取上市以来的交易日数。",
        "“到期前 20 个交易日内”用合同到期日（起息日 + 期限年数，发行时已知）与信号日后第 20 个交易日比较，不用东财 `EXPIRE_DATE`（它对提前到期/违约债是事后值）；超出日历的部分用工作日外推。",
        "正股涨跌停推断 ST 直接复用 `stock_mf` 的面板和规则（日历、前复权、退市股用腾讯数据），数据截断到 2023-12-31；找不到正股行情的债券视为非 ST。",
        "强制退出：每个交易日收盘检查持仓是否仍满足全部合格条件，**不含“当日有成交量>0”这一项**（持仓当天没成交不应触发卖出），其余条件（含 20 日均成交额 >= 500 万、收盘价 <= 150）一律触发次日开盘卖出（这是对协议“变得不合格”的字面读法；敏感性版本 SMALL_EXITEVT 的强制退出不含流动性条件，不进入判定）。"
        f"开发期 SMALL 决策版强制退出按原因：" + "、".join(f"{k} {v} 次" for k, v in cause.items()) + "。",
        "新买入每只的预算 = 成交当日开盘时的组合市值/10，再受可用现金限制；SMALL 账户按 10 张整数倍向下取整，余下现金按 SHIBOR 计息。被阻挡的买单每个开盘重试直到被下一次信号替换；卖单一直重试。",
        "涨跌幅限制按协议自 2022-08-01 起 ±20%（开盘价到达限价即阻挡，价格按 3 位小数取整），此前不限制；生效日未经一手来源核实（协议已声明）。",
        "退市终值：赎回/到期按最后一根日线收盘价折现金，被动退市按 0%（决定版）或 50%（敏感性）；终值转换不计佣金和滑点。",
        "被动/赎回/到期的判型：规则 = 同一正股存在 404xxx 退债代码则被动；最后交易日在合同到期日前 30 天内则到期；东财赎回公告日不晚于最后交易日则赎回；否则被动。之后用巨潮公告标题逐只核对，人工修正见 `delist_overrides.json`。分类用到未截断的日线文件，只用于判定债券在开发期内是否真的终止（唯一读到开发期之后的一个布尔值），不产生任何收益数字。",
        "东财 `NOTICE_DATE_SH/HS` 不总是真正的赎回公告日（有的比巨潮公告早数月到一年多），协议规定“公告日当天及之前”即剔除，按协议字面执行，因此这部分债券从该日期起被提前剔出合格池（见第五节的差异统计）。",
        "CB_INDEX 用腾讯指数日线 sh000832 的收盘价（价格指数，协议已声明）；CSI300 用 `cn_transfer` 的 510300 复权收盘价（总收益）；现金和无风险利率用 `cn_transfer` 的 SHIBOR 3M（上一交易日利率，实际天数/365）。",
        "指标由 `stock_mf.metrics.period_metrics` 计算（252 日年化，超额夏普 = 日超额收益均值/标准差×√252）；基础净值是区间前一个交易日的净值，所以 DEV 区间的收益从 2017-12-29 收盘算起（该日是 2018 年首个信号日）。",
        f"{ds['n_fail']} 项下载失败；404xxx 无行情。" ,
    ]
    return d
