"""Self-contained HTML renderer: inline CSS, inline SVG charts, no scripts, no external resources."""
from __future__ import annotations

import math
from datetime import datetime
from html import escape as _esc

from .config import SHANGHAI
from .safety import sanitize

NOTE = "冻结规则，仅供观察，不构成投资建议"
MARKET_ORDER = ("US", "CN")

CSS = """
:root{--bg:#f4f5f7;--card:#fff;--fg:#1c2330;--muted:#6a7485;--border:#dfe3ea;--accent:#2f6fed;--good:#0f8f6b;
--bad:#cf4a3f;--warn-bg:#fff4dc;--warn-fg:#7a5200;--err-bg:#fdeceb;--err-fg:#8a1f17;--info-bg:#eaf1ff;--info-fg:#1d4fb8;
--s1:#2f6fed;--s2:#e08a1e;--s3:#8a5cd6;--grid:#e6e9ef;--chip:#eef0f4}
@media (prefers-color-scheme:dark){:root{--bg:#10141b;--card:#181e28;--fg:#e6eaf2;--muted:#93a0b4;--border:#2a3342;
--accent:#6ea0ff;--good:#3ecf9e;--bad:#ff7b72;--warn-bg:#3a2e12;--warn-fg:#f2cf7a;--err-bg:#3d1c1a;--err-fg:#ffb4ad;
--info-bg:#1b2c4d;--info-fg:#a9c6ff;--s1:#6ea0ff;--s2:#f0a64a;--s3:#b695f2;--grid:#263040;--chip:#222a37}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC",
"Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;font-variant-numeric:tabular-nums}
.wrap{max-width:1180px;margin:0 auto;padding:16px}
header.top{margin-bottom:16px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:19px;margin:0}
h3{font-size:15px;margin:0 0 8px}
.sub{color:var(--muted);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0}
.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:12.5px;color:var(--muted)}
.disclaimer{margin-top:8px;font-size:13px;color:var(--warn-fg);background:var(--warn-bg);border-radius:8px;padding:6px 10px}
.markets{display:grid;grid-template-columns:1fr;gap:16px}
@media (min-width:1040px){.markets{grid-template-columns:1fr 1fr;align-items:start}}
.market{min-width:0}
.mhead{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 12px;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:12px;min-width:0}
.card.preview{border-style:dashed;border-color:var(--accent)}
.badge{display:inline-block;border-radius:6px;padding:0 7px;font-size:12px;font-weight:600;margin-left:6px}
.b-info{background:var(--info-bg);color:var(--info-fg)}.b-warn{background:var(--warn-bg);color:var(--warn-fg)}
.b-good{background:var(--chip);color:var(--good)}.b-bad{background:var(--chip);color:var(--bad)}
.b-mute{background:var(--chip);color:var(--muted)}
.banner{border-radius:10px;padding:10px 12px;margin-bottom:12px;font-size:14px}
.banner.info{background:var(--info-bg);color:var(--info-fg)}
.banner.warn{background:var(--warn-bg);color:var(--warn-fg)}
.banner.err{background:var(--err-bg);color:var(--err-fg)}
.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px}
@media (min-width:520px){.kpis{grid-template-columns:repeat(3,1fr)}}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:8px 10px;min-width:0}
.kpi .l{font-size:12px;color:var(--muted)}.kpi .v{font-size:18px;font-weight:650;overflow-wrap:anywhere}
.kpi .s{font-size:12px;color:var(--muted)}
.pos{color:var(--good)}.neg{color:var(--bad)}.mut{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:6px 6px;text-align:right;border-bottom:1px solid var(--border);white-space:nowrap}
th:first-child,td:first-child{text-align:left;white-space:normal}
th{font-weight:600;color:var(--muted);font-size:12.5px}
tr:last-child td{border-bottom:0}
.scroll{overflow-x:auto}
svg{display:block;width:100%;height:auto}
svg text{fill:var(--muted);font:12px -apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:6px;font-size:12.5px;color:var(--muted)}
.legend i{display:inline-block;width:18px;height:0;border-top:3px solid;vertical-align:middle;margin-right:5px}
.note{font-size:12.5px;color:var(--muted);margin:6px 0 0}
details{margin-top:6px;font-size:13px;color:var(--muted)}
.kind-add{color:var(--good);font-weight:600}.kind-del{color:var(--bad);font-weight:600}
.kind-chg{color:var(--info-fg);font-weight:600}
footer{margin:8px 0 24px;font-size:12.5px;color:var(--muted)}
"""


def e(x) -> str:
    return _esc(str(x), quote=True)


def pct(x, digits: int = 2, sign: bool = True) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def pcls(x) -> str:
    if x is None or abs(x) < 1e-12:
        return "mut"
    return "pos" if x > 0 else "neg"


def money(x) -> str:
    return "—" if x is None else f"{x:,.0f}"


def _name(view, sym) -> str:
    return "现金" if sym == "CASH" else f"{sym} {view['names'].get(sym, '')}".strip()


# ------------------------------------------------------------------------------------------------ charts
def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi - lo < 1e-9:
        hi, lo = hi + 0.005, lo - 0.005
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = math.floor(lo / step) * step
    ticks, t = [], start
    while t <= hi + step * 0.999:
        ticks.append(round(t, 10))
        t += step
    return ticks


def nav_chart(view: dict) -> str:
    s = view["series"]
    if not s or len(s["dates"]) < 2:
        return ""
    init = view["initial_nav"]
    series = [("strat", "CAP_DEF_M 影子组合", "var(--s1)", ""), ("hold", view["bench_hold_label"], "var(--s2)", "5 3"),
              ("mix", view["bench_mix_label"], "var(--s3)", "2 3")]
    data = {k: [v / init - 1.0 for v in s[k]] for k, *_ in series}
    allv = [0.0] + [v for k in data for v in data[k]]
    ticks = _nice_ticks(min(allv), max(allv))
    lo, hi = ticks[0], ticks[-1]
    W, H, L, R, T, B = 640, 260, 52, 14, 12, 28
    n = len(s["dates"])
    x = lambda i: L + (W - L - R) * (i / (n - 1))
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="净值对比图：影子组合与两个基准自冻结后的累计收益">']
    for t in ticks:
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="var(--grid)" '
                   f'stroke-width="{1.6 if abs(t) < 1e-12 else 1}"/>')
        out.append(f'<text x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t * 100:.1f}%</text>')
    idxs = sorted({0, n - 1, *(round(i * (n - 1) / 3) for i in (1, 2))})
    for i in idxs:
        anchor = "start" if i == 0 else "end" if i == n - 1 else "middle"
        out.append(f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="{anchor}">{e(s["dates"][i][2:])}</text>')
    for k, label, color, dash in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(data[k]))
        da = f' stroke-dasharray="{dash}"' if dash else ""
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="2.2"{da} points="{pts}"/>')
    out.append("</svg>")
    leg = "".join(f'<span><i style="border-color:{c};{"border-top-style:dashed;" if d else ""}"></i>{e(l)} '
                  f'<b class="{pcls(data[k][-1])}">{pct(data[k][-1])}</b></span>' for k, l, c, d in series)
    return "".join(out) + f'<div class="legend">{leg}</div>'


def trend_chart(view: dict) -> str:
    rows = view["trend"]
    vals = [abs(r["dist"]) for r in rows if r["dist"] is not None]
    if not vals:
        return ""
    cap = max(0.05, min(max(vals), 0.40))
    W, LBL, RV, rh = 520, 128, 56, 26
    H = rh * len(rows) + 14
    mid = LBL + (W - LBL - RV) / 2
    half = (W - LBL - RV) / 2
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="各资产相对40周均线的偏离幅度">',
           f'<line x1="{mid:.1f}" x2="{mid:.1f}" y1="2" y2="{H - 10}" stroke="var(--border)" stroke-width="1.5"/>']
    for i, r in enumerate(rows):
        yy = 6 + i * rh
        out.append(f'<text x="{LBL - 8}" y="{yy + 15}" text-anchor="end" style="fill:var(--fg)">'
                   f'{e(r["symbol"])} {e(r["name"])}</text>')
        if r["dist"] is None:
            out.append(f'<text x="{mid + 6:.1f}" y="{yy + 15}">数据不足</text>')
            continue
        d = r["dist"]
        ln = min(abs(d) / cap, 1.0) * (half - 4)
        color = "var(--good)" if r["passes"] else "var(--bad)"
        x0 = mid if d >= 0 else mid - ln
        out.append(f'<rect x="{x0:.1f}" y="{yy + 3}" width="{max(ln, 1):.1f}" height="16" rx="3" fill="{color}"/>')
        out.append(f'<text x="{W - RV + 8}" y="{yy + 15}" style="fill:var(--fg)">{pct(d, 1)}</text>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------------------------------------ sections
def _kpi(label, value, sub="", cls="") -> str:
    return (f'<div class="kpi"><div class="l">{e(label)}</div><div class="v {cls}">{value}</div>'
            f'<div class="s">{sub}</div></div>')


def _weights_table(view, weights: dict, changes: list | None = None) -> str:
    kinds = {c["symbol"]: c for c in (changes or [])}
    rows = []
    syms = [s for s in list(view["universe"]) + ["CASH"] if weights.get(s, 0) > 1e-9 or (s in kinds and kinds[s]["kind"] != "不变")]
    for s in syms:
        c = kinds.get(s)
        tag = ""
        if c and c["kind"] != "不变":
            cls = {"新增": "kind-add", "清仓": "kind-del"}.get(c["kind"], "kind-chg")
            tag = f'<span class="{cls}">{c["kind"]}</span> <span class="mut">({pct(c["from"], 0, False)} → {pct(c["to"], 0, False)})</span>'
        elif c:
            tag = '<span class="mut">不变</span>'
        rows.append(f'<tr><td>{e(_name(view, s))}</td><td>{pct(weights.get(s, 0.0), 1, False)}</td>'
                    + (f"<td>{tag}</td>" if changes is not None else "") + "</tr>")
    head = "<tr><th>资产</th><th>权重</th>" + ("<th>相对当前</th>" if changes is not None else "") + "</tr>"
    return f'<div class="scroll"><table>{head}{"".join(rows)}</table></div>'


def _banner_empty(view) -> str:
    nr, fe = view["next_rebalance"], view["first_execution"]
    est = "（预计）" if nr["estimated"] else ""
    if fe and not view["window"]["empty"]:
        return ""
    if fe:
        txt = (f"冻结后首个信号已于 {e(view['first_execution_signal'])} 收盘产生，将于 {e(fe)} 开盘执行；"
               f"此前影子组合为空仓，暂无净值。")
    else:
        txt = (f"冻结后首个月末信号预计在 {e(nr['signal_session'])} 收盘产生，{e(nr['execution_session'])} 开盘执行{est}；"
               f"在此之前影子组合为空仓，尚无净值与基准对比。")
    return (f'<div class="banner info"><b>前瞻期刚开始，尚无足够数据。</b> {txt}'
            f'下方仍展示冻结时的参考目标组合、趋势线距离、月末预览与下次调仓日期。</div>')


def _section_ok(v: dict) -> str:
    out = []
    src = v["sources"]
    src_txt = "、".join(sorted(set(src.values()))) if src else "—"
    badge = '<span class="badge b-warn">使用备用数据源</span>' if v["fallback_used"] else '<span class="badge b-good">主数据源</span>'
    out.append(f'<div class="mhead"><h2>{e(v["title"])}</h2><span class="sub">数据截至 {e(v["as_of"])} 收盘 · '
               f'数据源 {e(src_txt)}{badge} · 现金收益 {e(v["rate_source"])}</span></div>')
    out.append(_banner_empty(v))
    w = v["window"]
    if not w["empty"] and w["n_sessions"] < 5:
        out.append(f'<div class="banner warn">前瞻期仅 {w["n_sessions"]} 个交易日，收益与回撤暂无统计意义。</div>')

    # KPI tiles
    if not w["empty"]:
        st, bh, bm = v["strategy"], v["bench_hold"], v["bench_mix"]
        k = [_kpi("影子组合净值", money(st["nav"]), f'初始 {money(v["initial_nav"])}'),
             _kpi("当日收益", pct(st["day_return"]), "", pcls(st["day_return"])),
             _kpi("冻结后累计收益", pct(st["cum_return"]), "", pcls(st["cum_return"])),
             _kpi(v["bench_hold_label"], pct(bh["cum_return"]), "冻结后累计", pcls(bh["cum_return"])),
             _kpi(v["bench_mix_label"], pct(bm["cum_return"]), "冻结后累计", pcls(bm["cum_return"])),
             _kpi("距峰值回撤", pct(st["drawdown"]), f'最大回撤 {pct(st["max_drawdown"])}', pcls(st["drawdown"]))]
    else:
        k = [_kpi("影子组合净值", "—", f'初始 {money(v["initial_nav"])}，首次执行后开始'), _kpi("当日收益", "—"),
             _kpi("冻结后累计收益", "—"), _kpi(v["bench_hold_label"], "—"), _kpi(v["bench_mix_label"], "—"),
             _kpi("距峰值回撤", "—")]
    out.append(f'<div class="kpis">{"".join(k)}</div>')

    # NAV chart
    out.append('<div class="card"><h3>净值对比（冻结后累计收益）</h3>')
    ch = nav_chart(v)
    out.append(ch if ch else '<p class="mut">样本不足 2 个交易日，暂不绘制净值曲线。</p>')
    out.append("</div>")

    # holdings
    out.append('<div class="card"><h3>当前持仓</h3>')
    h = v["holdings"]
    if h:
        rows = "".join(
            f'<tr><td>{e(_name(v, r["symbol"]))}</td><td>{pct(r["weight"], 1, False)}</td><td>{e(r["entry_date"])}</td>'
            f'<td class="{pcls(r["ret_since_entry"])}">{pct(r["ret_since_entry"])}</td></tr>' for r in h["rows"])
        if h["cash_weight"] > 0.0005:
            rows += f'<tr><td>现金</td><td>{pct(h["cash_weight"], 1, False)}</td><td>—</td><td>—</td></tr>'
        out.append(f'<div class="scroll"><table><tr><th>资产</th><th>当前权重</th><th>入场日</th><th>入场后收益</th></tr>{rows}</table></div>')
        out.append(f'<p class="note">最近一次调仓：{e(h["rebalance_date"])} 开盘（信号 {e(h["signal_session"])} 收盘），'
                   f'单边换手 {pct(h["last_turnover"], 1, False)}，费用 {money(h["last_fee"])}。入场后收益按入场日开盘价计算。</p>')
    else:
        out.append('<p class="mut">影子组合尚未建仓（首次执行前为空仓）。</p>')
    rt = v["reference_target"]
    pre = "冻结前，仅供参考" if rt["pre_freeze"] else "冻结后"
    out.append(f'<h3 style="margin-top:12px">最新月末信号目标组合 <span class="badge b-mute">{pre}</span></h3>')
    out.append(_weights_table(v, rt["weights"]))
    out.append(f'<p class="note">信号日 {e(rt["signal_session"])} 收盘，{e(rt["execution_session"])} 开盘执行'
               f'（状态：{"已执行" if rt["status"] == "COMPLETE" else "待执行"}）。</p></div>')

    # what-if
    wi = v["what_if"]
    verdict = ('<span class="badge b-warn">与基准组合不同</span>' if wi["changed"]
               else '<span class="badge b-good">与基准组合一致</span>')
    out.append(f'<div class="card preview"><h3>月末预览（假设）<span class="badge b-info">预览，非交易</span>{verdict}</h3>')
    out.append(f'<p class="note" style="margin-top:0">假设 {e(wi["signal_session"])} 收盘即为月末信号收盘，'
               f'冻结规则会给出下列目标组合（若成真将于 {e(wi["execution_session_if"])} 开盘执行）。对比基准：{e(wi["basis_label"])}。</p>')
    out.append(_weights_table(v, wi["weights"], wi["changes"]))
    di = wi.get("defensive_info")
    if di:
        ok = di["asset_ret"] > di["bill_ret"]
        out.append(f'<p class="note">防御槽检验：{e(v["names"].get(_def_sym(v), ""))} 近3个月收益 {pct(di["asset_ret"])}，'
                   f'同期现金收益 {pct(di["bill_ret"])}，{"高于现金，空闲仓位配置防御资产" if ok else "不高于现金，空闲仓位留作现金"}。</p>')
    out.append("</div>")

    # trend
    out.append('<div class="card"><h3>距 40 周趋势线</h3>')
    out.append(trend_chart(v))
    rows = []
    for r in v["trend"]:
        if r["dist"] is None:
            rows.append(f'<tr><td>{e(_name(v, r["symbol"]))}</td><td colspan="4" class="mut">数据不足</td></tr>')
            continue
        okc = "pos" if r["passes"] else "neg"
        off = "—" if r["passes_official"] is None else ("通过" if r["passes_official"] else "未通过")
        rows.append(f'<tr><td>{e(_name(v, r["symbol"]))}</td><td>{r["close"]:.3f}</td><td>{r["sma"]:.3f}</td>'
                    f'<td class="{pcls(r["dist"])}">{pct(r["dist"])}</td>'
                    f'<td class="{okc}">{"通过" if r["passes"] else "未通过"}</td><td class="mut">{off}</td></tr>')
    out.append('<div class="scroll"><table><tr><th>资产</th><th>最新收盘</th><th>40周均线</th><th>偏离</th>'
               f'<th>今日判定</th><th>上周收盘判定</th></tr>{"".join(rows)}</table></div>')
    out.append('<p class="note">偏离 = 最新收盘 / 40周均线 − 1，均线由冻结的趋势函数计算（含最新收盘作为本周收盘，严格大于才通过）。'
               '“上周收盘判定”为最近一个完整周的正式结果。</p></div>')

    # next rebalance
    nr = v["next_rebalance"]
    est = ' <span class="badge b-warn">预计</span>' if nr["estimated"] else ""
    out.append('<div class="card"><h3>下次调仓</h3>'
               f'<p style="margin:0">月末信号：<b>{e(nr["signal_session"])}</b> 收盘 → 执行：<b>{e(nr["execution_session"])}</b> 开盘{est}</p>'
               f'<p class="note">还有 {nr["sessions_left"]} 个交易日 / {nr["days_left"]} 个自然日。</p></div>')

    # notes
    notes = list(v["quality_notes"]) + list(v["fetch_notes"])
    if v.get("postponements"):
        notes.append("存在因停牌而顺延的调仓：" + "；".join(
            f'{p["signal_session"]} 信号→{p["actual_execution"]}' for p in v["postponements"]))
    if notes:
        out.append(f'<details><summary>数据备注（{len(notes)}）</summary><ul>'
                   + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul></details>")
    return "".join(out)


def _def_sym(v) -> str:
    # defensive bond is the universe member named in the defensive test: last bond-like entry is carried in names
    return v.get("def_symbol") or ("IEF" if "IEF" in v["universe"] else "511010")


def _section_err(v: dict) -> str:
    ls = v.get("last_success")
    return (f'<div class="mhead"><h2>{e(v["title"])}</h2></div>'
            f'<div class="banner err"><b>该市场数据获取或计算失败。</b><br>{e(sanitize(v.get("message", "")))}<br>'
            f'最后一次成功：{e(ls) if ls else "无记录"}</div>')


def render_page(views: dict, generated_at: datetime, last_success: dict | None = None) -> str:
    gen = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    chips = [f'<span class="chip">生成时间 {gen}（Asia/Shanghai）</span>']
    secs = []
    for key in MARKET_ORDER:
        v = views.get(key)
        if v is None:
            continue
        if v["status"] == "ok":
            chips.append(f'<span class="chip">{e(v["title"])}数据截至 {e(v["as_of"])}</span>')
            secs.append(f'<section class="market">{_section_ok(v)}</section>')
        else:
            v = {**v, "last_success": v.get("last_success") or (last_success or {}).get(key)}
            chips.append(f'<span class="chip">{e(v["title"])}：失败（最后成功 {e(v["last_success"] or "无")}）</span>')
            secs.append(f'<section class="market">{_section_err(v)}</section>')
    ok = [v for v in views.values() if v["status"] == "ok"]
    fz = ok[0]["freeze_date"] if ok else "2026-10-19"
    chips.append(f'<span class="chip">冻结日 {e(fz)}</span>')
    body = (f'<div class="wrap"><header class="top"><h1>CAP_DEF_M 前瞻影子盘看板</h1>'
            f'<div class="sub">冻结策略 CAP_DEF_M · 月度调仓 · 40周趋势 · 初始资金 1,000,000 · 成本 5bp + 滑点 5bp · 现金按短期利率计息</div>'
            f'<div class="chips">{"".join(chips)}</div><div class="disclaimer">{NOTE}</div></header>'
            f'<main class="markets">{"".join(secs)}</main>'
            f'<footer>本页由本地脚本自动生成，不含任何外部资源。美股使用 yfinance；A股优先使用 同花顺(THS) 接口，失败时依次回退新浪与东方财富。'
            f'{NOTE}。</footer></div>')
    html = (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>CAP_DEF_M 前瞻影子盘</title>'
            f'<style>{CSS}</style></head><body>{body}</body></html>')
    return sanitize(html)
