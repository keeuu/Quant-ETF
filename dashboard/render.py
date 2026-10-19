"""Self-contained HTML renderer: inline CSS, inline SVG charts, no scripts, no external resources."""
from __future__ import annotations

import math
from datetime import datetime
from html import escape as _esc

from .config import SHANGHAI
from .safety import sanitize

NOTE = "冻结规则 · 仅供参考，不构成投资建议"
MARKET_ORDER = ("US", "CN")
SOON_SESSIONS = 2          # "rebalance soon" threshold, in trading sessions
TREND_WARN = 0.03          # a held asset closer than this to its trend line is worth a mention

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--muted:#6a7485;--border:#e1e5ec;--accent:#2f6fed;
--warn-bg:#fff4dc;--warn-fg:#7a5200;--err-bg:#fdeceb;--err-fg:#8a1f17;--info-bg:#eaf1ff;--info-fg:#1d4fb8;
--s1:#2f6fed;--s2:#e08a1e;--s3:#8a5cd6;--grid:#e6e9ef;--chip:#eef0f4;--bar:#c9d6f5}
@media (prefers-color-scheme:dark){:root{--bg:#10141b;--card:#181e28;--fg:#e6eaf2;--muted:#93a0b4;--border:#2a3342;
--accent:#6ea0ff;--warn-bg:#3a2e12;--warn-fg:#f2cf7a;--err-bg:#3d1c1a;--err-fg:#ffb4ad;
--info-bg:#1b2c4d;--info-fg:#a9c6ff;--s1:#6ea0ff;--s2:#f0a64a;--s3:#b695f2;--grid:#263040;--chip:#222a37;--bar:#34508a}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",
"Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif;font-variant-numeric:tabular-nums}
.wrap{max-width:720px;margin:0 auto;padding:20px 16px 32px}
header.top{margin-bottom:28px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:20px;margin:0}
h3{font-size:15px;margin:0 0 8px}
.sub{color:var(--muted);font-size:13px}
.market{margin-bottom:44px;min-width:0}
.mhead{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;margin:0 0 12px}
.status{border-radius:14px;padding:18px 18px;font-size:22px;line-height:1.45;font-weight:650;margin-bottom:20px;
background:var(--card);border:1px solid var(--border)}
.status.act{background:var(--warn-bg);color:var(--warn-fg);border-color:var(--warn-fg)}
.status.err{background:var(--err-bg);color:var(--err-fg);border-color:var(--err-fg)}
.status.soon{background:var(--info-bg);color:var(--info-fg);border-color:var(--info-fg)}
.block{margin-bottom:20px}
.block>h3{color:var(--muted);font-weight:600;font-size:13.5px}
.hold{display:grid;grid-template-columns:minmax(90px,1.1fr) 3fr 52px;align-items:center;gap:4px 10px;margin:4px 0}
.hold .bar{height:12px;border-radius:6px;background:var(--chip);overflow:hidden}
.hold .bar i{display:block;height:100%;background:var(--bar);border-radius:6px}
.hold .pc{text-align:right;font-weight:600}
.risk{font-size:15px;padding:10px 14px;border-radius:10px;background:var(--chip);margin:0 0 6px}
.nums{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.num{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:8px 10px;min-width:0}
.num .l{font-size:12px;color:var(--muted)}.num .v{font-size:19px;font-weight:650;overflow-wrap:anywhere}
.quiet{color:var(--muted);font-size:14px;margin:0}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:12px;min-width:0}
.card.preview{border-style:dashed;border-color:var(--accent)}
.badge{display:inline-block;border-radius:6px;padding:0 7px;font-size:12px;font-weight:600;margin-left:6px}
.b-info{background:var(--info-bg);color:var(--info-fg)}.b-warn{background:var(--warn-bg);color:var(--warn-fg)}
.b-good,.b-bad,.b-mute{background:var(--chip);color:var(--muted)}
.banner{border-radius:10px;padding:10px 12px;margin-bottom:12px;font-size:14px}
.banner.info{background:var(--info-bg);color:var(--info-fg)}
.banner.warn{background:var(--warn-bg);color:var(--warn-fg)}
.banner.err{background:var(--err-bg);color:var(--err-fg)}
.mut{color:var(--muted)}
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
details{margin:8px 0;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:0 14px}
details>summary{cursor:pointer;padding:12px 0;font-size:15px;font-weight:600;list-style-position:inside}
details[open]>summary{border-bottom:1px solid var(--border);margin-bottom:12px}
details>div,details>ul{padding-bottom:12px}
.kind-add,.kind-del,.kind-chg,td.buy,td.sell{font-weight:600}
.card.alerts ul{margin:4px 0 0;padding-left:20px}
footer{margin:8px 0 24px;font-size:12.5px;color:var(--muted)}
"""


def e(x) -> str:
    return _esc(str(x), quote=True)


def pct(x, digits: int = 2, sign: bool = True) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    return f"{x * 100:+.{digits}f}%" if sign else f"{x * 100:.{digits}f}%"


def pcls(x) -> str:
    return ""                       # numbers stay neutral: direction is shown by +/- and arrows, not colour


def apct(x, digits: int = 2) -> str:
    """Signed percentage with an arrow, e.g. '▲ +1.20%' / '▼ −0.80%' (neutral colour)."""
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    arrow = "▲" if x > 1e-12 else "▼" if x < -1e-12 else "•"
    return f"{arrow} {x * 100:+.{digits}f}%".replace("-", "−")


def cn_date(d) -> str:
    """'2026-11-02' -> '11月2日'."""
    try:
        y, m, dd = str(d).split("-")
        return f"{int(m)}月{int(dd)}日"
    except Exception:  # noqa: BLE001
        return str(d)


def plain(view, sym) -> str:
    return "现金" if sym == "CASH" else view["names"].get(sym, sym)


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
    series = [("strat", "本策略", "var(--s1)", ""), ("hold", view["bench_hold_label"], "var(--s2)", "5 3"),
              ("mix", view["bench_mix_label"], "var(--s3)", "2 3")]
    data = {k: [v / init - 1.0 for v in s[k]] for k, *_ in series}
    allv = [0.0] + [v for k in data for v in data[k]]
    ticks = _nice_ticks(min(allv), max(allv))
    lo, hi = ticks[0], ticks[-1]
    W, H, L, R, T, B = 640, 260, 52, 14, 12, 28
    n = len(s["dates"])
    x = lambda i: L + (W - L - R) * (i / (n - 1))
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="净值对比图：本策略与两个基准自冻结后的累计收益">']
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
                  f'<b class="{pcls(data[k][-1])}">{apct(data[k][-1])}</b></span>' for k, l, c, d in series)
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
        color = "var(--s1)" if r["passes"] else "var(--muted)"
        x0 = mid if d >= 0 else mid - ln
        out.append(f'<rect x="{x0:.1f}" y="{yy + 3}" width="{max(ln, 1):.1f}" height="16" rx="3" fill="{color}"/>')
        out.append(f'<text x="{W - RV + 8}" y="{yy + 15}" style="fill:var(--fg)">{apct(d, 1)}</text>')
    out.append("</svg>")
    return "".join(out)


# ------------------------------------------------------------------------------------------------ sections
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



def _alerts_card(info: dict | None, key: str) -> str:
    """"提醒" card: active alerts of this market and the last notification time. Empty string if alerts did not run."""
    if not info or key not in info.get("markets", {}):
        return ""
    m = info["markets"][key]
    last = "—"
    if m.get("last_notified"):
        try:
            last = datetime.fromisoformat(m["last_notified"]).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            last = m["last_notified"]
    if not info.get("enabled", True):
        state = "通知已关闭（alerts.json）"
    elif not info.get("notify", True):
        state = "本次运行未发送通知（--no-notify 或回放）"
    else:
        state = "通知已开启"
    items = "".join(
        f'<li><span class="badge {"b-warn" if a.get("new") else "b-mute"}">{e(a["code"])}</span> {e(a["text"])}</li>'
        for a in m["alerts"])
    body = f"<ul>{items}</ul>" if items else '<p class="mut" style="margin:4px 0 0">当前没有需要关注的提醒。</p>'
    return (f'<!--alerts--><div class="card alerts"><h3>提醒</h3>{body}'
            f'<p class="note">上次通知：{e(last)} · {e(state)}。提醒仅供查看，不影响信号、净值与调仓清单。</p></div><!--/alerts-->')



def _qty(x: float, frac: bool) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".") if frac else f"{x:,.0f}"


def _orders_card(v: dict) -> str:
    o = v.get("orders")
    if not o:
        return ""
    if o.get("error"):
        return (f'<div class="banner warn">调仓清单计算失败（不影响信号与净值）：{e(o["error"])}</div>')
    cur, frac = o["currency"], o["fractional"]
    out = [f'<div class="banner warn" style="margin-bottom:8px">{e(o["disclaimer"])}。</div>',
           f'<p class="note" style="margin-top:0">账户资金 {o["capital"]:,.0f} {e(cur)}（可在 dashboard/account.json 修改）；'
           f'价格为 {e(o["price_date"])} 收盘价，实际成交价会不同；现金代理 {e(o["cash_proxy"])}'
           f'（{e(o["proxy_note"])} {o["proxy_price"]:,.3f}）。'
           + (f'沪深ETF按整手(每手 {o["lot"]:.0f} 份)，手续费 max({o["min_fee"]:g} 元, 万分之{o["commission_bp"]:g})/笔。'
              if not frac else "美股可买碎股(Alpaca)，同时给出整股数；佣金 0。") + "</p>"]
    for sec in o["sections"]:
        preview = sec["kind"] == "preview"
        out.append(f'<h3 style="margin-top:12px">{e(sec["title"])}'
                   f'<span class="badge {"b-info" if preview else "b-good"}">{"预览" if preview else "正式"}</span></h3>')
        out.append(f'<p class="note" style="margin-top:0">起点：{e(sec["basis_label"])}。目标：{e(sec["target_label"])}。</p>')
        rows = []
        for r in sec["rows"]:
            if r["side"] == "持有" and r["cur_shares"] <= 1e-9 and r["tgt_shares"] <= 1e-9:
                continue
            cls = {"买入": "buy", "卖出": "sell"}.get(r["side"], "mut")
            nm = e(f'{r["symbol"]} {r["name"]}') + (' <span class="badge b-mute">现金代理</span>' if r["is_proxy"] else "")
            cells = (f'<td>{nm}</td><td class="{cls}">{r["side"]}</td><td>{_qty(abs(r["delta"]), frac)}</td>'
                     f'<td>{_qty(r["cur_shares"], frac)} → {_qty(r["tgt_shares"], frac)}</td><td>{r["price"]:,.3f}</td>'
                     f'<td>{r["amount"]:,.0f}</td><td>{r["fee"]:,.2f}</td>')
            if frac:
                cells += f'<td>{_qty(abs(r["whole_delta"]), False)} ({_qty(r["whole_cur"], False)} → {_qty(r["whole_tgt"], False)})</td>'
            rows.append(f"<tr>{cells}</tr>")
        head = ("<tr><th>资产</th><th>方向</th><th>数量(份)</th><th>持仓变化</th><th>参考价</th><th>金额</th><th>预计费用</th>"
                + ("<th>整股方案(变化)</th>" if frac else "") + "</tr>")
        body = "".join(rows) or f'<tr><td colspan="{8 if frac else 7}" class="mut">无需操作</td></tr>'
        out.append(f'<div class="scroll"><table>{head}{body}</table></div>')
        left = (f'调仓后剩余现金 <b>{sec["cash_after"]:,.2f}</b> {e(cur)}' + (f'（整股方案 {sec["cash_after_whole"]:,.2f}）' if frac else "")
                + f'；预计手续费合计 <b>{sec["fees"]:,.2f}</b>；成交额合计 {sec["turnover_amount"]:,.0f}。')
        if not frac and sec.get("proxy_lot_cost"):
            left += (f' 剩余资金不足一手现金代理（一手约 {sec["proxy_lot_cost"]:,.0f}）时留作账户现金；'
                     f'其余空闲资金已计入 {e(o["cash_proxy"])}。')
        out.append(f'<p class="note">{left}</p>')
        for n in sec["notes"]:
            out.append(f'<p class="note">注：{e(n)}</p>')
    return "".join(out)


def _def_sym(v) -> str:
    # defensive bond is the universe member named in the defensive test: last bond-like entry is carried in names
    return v.get("def_symbol") or ("IEF" if "IEF" in v["universe"] else "511010")



# ------------------------------------------------------------------------------------------------ plain-language top level
def _order_count(v: dict) -> int | None:
    """Number of buy/sell lines in the list that matters (official if pending, otherwise the month-end preview)."""
    o = v.get("orders")
    if not o or o.get("error"):
        return None
    secs = o["sections"]
    sec = next((s for s in secs if s["kind"] == "official"), secs[0] if secs else None)
    if sec is None:
        return 0
    return sum(1 for r in sec["rows"] if r["side"] in ("买入", "卖出"))


def _pending(v: dict) -> bool:
    rt = v["reference_target"]
    return rt["status"] != "COMPLETE" and not rt["pre_freeze"]


def status_line(v: dict) -> tuple[str, str]:
    """(css class, plain sentence) for the one-line 'what to do now'."""
    nr = v["next_rebalance"]
    if _pending(v):
        return "act", f"有新的调仓：请在 {cn_date(v['reference_target']['execution_session'])} 开盘前按下方清单操作"
    if nr["sessions_left"] <= SOON_SESSIONS:
        n = max(1, nr["sessions_left"])
        return "soon", f"{n} 个交易日后调仓，届时看清单"
    est = "（预计）" if nr["estimated"] else ""
    return "", f"本月无需操作，下次调仓 {cn_date(nr['execution_session'])}{est}（还有 {nr['sessions_left']} 个交易日）"


def status_line_err(v: dict) -> tuple[str, str]:
    ls = v.get("last_success")
    return "err", f"数据未更新，最后成功日期 {cn_date(ls)}" if ls else "数据未更新，暂无成功记录"


def holdings_view(v: dict) -> tuple[str, list[tuple[str, float]]]:
    h = v["holdings"]
    if h:
        rows = [(plain(v, r["symbol"]), r["weight"]) for r in h["rows"]]
        if h["cash_weight"] > 0.0005:
            rows.append(("现金", h["cash_weight"]))
        return "当前持仓", rows
    w = v["reference_target"]["weights"]
    rows = sorted(((plain(v, s), float(w.get(s, 0.0))) for s in list(v["universe"]) + ["CASH"] if w.get(s, 0.0) > 1e-9),
                  key=lambda t: (t[0] == "现金", -t[1]))
    return "按规则当前应持有", rows


def risk_lines(v: dict) -> list[str]:
    """At most two short lines about what could change soon. Pure function of the view (no alert state)."""
    out: list[str] = []
    h = v["holdings"]
    if h:
        held = {r["symbol"] for r in h["rows"]}
    else:
        w = v["reference_target"]["weights"]
        held = {s for s in v["universe"] if w.get(s, 0.0) > 1e-9}
    near = [r for r in v["trend"] if r["symbol"] in held and r["dist"] is not None and abs(r["dist"]) <= TREND_WARN]
    if near:
        r = min(near, key=lambda r: abs(r["dist"]))
        nm = plain(v, r["symbol"])
        fate = "转为现金" if r["symbol"] == v.get("def_symbol") else "被换出"
        if r["dist"] >= 0:
            out.append(f"需留意：{nm} 离趋势线只剩 {pct(r['dist'], 1).replace('-', '−')}，跌破则{fate}")
        else:
            out.append(f"需留意：{nm} 已跌到趋势线下方 {pct(r['dist'], 1).replace('-', '−')}，若月末仍未回升则{fate}")
    ch = v["what_if"]["changes"]
    gone = [plain(v, c["symbol"]) for c in ch if c["kind"] == "清仓"]
    come = [plain(v, c["symbol"]) for c in ch if c["kind"] == "新增"]
    if gone or come:
        parts = []
        if gone:
            parts.append("、".join(gone) + (" 将被换出" if come else " 将被卖出"))
        if come:
            parts.append(("换入 " if gone else "新增 ") + "、".join(come))
        out.append("若今天就是月末：" + "，".join(parts))
    return out[:2]


def _holdings_block(v: dict) -> str:
    label, rows = holdings_view(v)
    if not rows:
        return f'<div class="block"><h3>{e(label)}</h3><p class="quiet">暂无持仓（空仓）。</p></div>'
    items = "".join(f'<div class="hold"><span>{e(n)}</span><span class="bar"><i style="width:{min(max(w, 0), 1) * 100:.1f}%"></i></span>'
                    f'<span class="pc">{pct(w, 0, False)}</span></div>' for n, w in rows)
    return f'<div class="block"><h3>{e(label)}</h3>{items}</div>'


def _perf_block(v: dict) -> str:
    w = v["window"]
    if w["empty"]:
        fe = v["first_execution"] or v["next_rebalance"]["execution_session"]
        return f'<div class="block"><p class="quiet">前瞻期刚开始，{cn_date(fe)}后开始显示收益。</p></div>'
    st, bh, bm = v["strategy"], v["bench_hold"], v["bench_mix"]
    nums = "".join(f'<div class="num"><div class="l">{e(l)}</div><div class="v">{apct(x["cum_return"])}</div></div>'
                   for l, x in (("本策略 累计收益", st), (v["bench_hold_label"], bh), (v["bench_mix_label"], bm)))
    few = (f'<p class="note">前瞻期仅 {w["n_sessions"]} 个交易日，收益与回撤暂无统计意义。</p>' if w["n_sessions"] < 5 else "")
    ch = nav_chart(v)
    dd = f'<p class="note">距最高点回撤 {apct(st["drawdown"])}，期间最大回撤 {apct(st["max_drawdown"])}。</p>'
    return (f'<div class="block"><h3>冻结以来的表现</h3><div class="nums">{nums}</div>{ch}{dd}{few}</div>')


def _details(title: str, body: str, open_: bool = False) -> str:
    if not body:
        return ""
    return f'<details{" open" if open_ else ""}><summary>{e(title)}</summary><div>{body}</div></details>'


def _trend_body(v: dict) -> str:
    out = [trend_chart(v)]
    rows = []
    for r in v["trend"]:
        if r["dist"] is None:
            rows.append(f'<tr><td>{e(_name(v, r["symbol"]))}</td><td colspan="5" class="mut">数据不足</td></tr>')
            continue
        off = "—" if r["passes_official"] is None else ("通过" if r["passes_official"] else "未通过")
        rows.append(f'<tr><td>{e(_name(v, r["symbol"]))}</td><td>{r["close"]:.3f}</td><td>{r["sma"]:.3f}</td>'
                    f'<td>{apct(r["dist"])}</td><td>{"通过" if r["passes"] else "未通过"}</td><td class="mut">{off}</td></tr>')
    out.append('<div class="scroll"><table><tr><th>资产</th><th>最新收盘</th><th>40周均线</th><th>偏离</th>'
               f'<th>今日判定</th><th>上周收盘判定</th></tr>{"".join(rows)}</table></div>')
    out.append('<p class="note">偏离 = 最新收盘 / 40周均线 − 1（趋势线即40周均线）。高于趋势线才会被持有；'
               '“上周收盘判定”为最近一个完整周的正式结果。</p>')
    return "".join(out)


def _preview_body(v: dict) -> str:
    out = []
    wi = v["what_if"]
    verdict = ('<span class="badge b-warn">与当前组合不同</span>' if wi["changed"]
               else '<span class="badge b-good">与当前组合一致</span>')
    out.append(f'<div class="card preview"><h3>月末预览（假设）<span class="badge b-info">预览，非交易</span>{verdict}</h3>')
    out.append(f'<p class="note" style="margin-top:0">假设 {e(wi["signal_session"])} 收盘即为月末，'
               f'规则会给出下列目标组合（若成真将于 {e(wi["execution_session_if"])} 开盘执行）。对比：{e(wi["basis_label"])}。</p>')
    out.append(_weights_table(v, wi["weights"], wi["changes"]))
    di = wi.get("defensive_info")
    if di:
        ok = di["asset_ret"] > di["bill_ret"]
        out.append(f'<p class="note">防守仓位说明：{e(v["names"].get(_def_sym(v), ""))} 近3个月收益 {pct(di["asset_ret"])}，'
                   f'同期现金收益 {pct(di["bill_ret"])}，{"高于现金，空闲仓位配置防守资产" if ok else "不高于现金，空闲仓位留作现金"}。</p>')
    out.append("</div>")
    nr = v["next_rebalance"]
    est = ' <span class="badge b-warn">预计</span>' if nr["estimated"] else ""
    out.append('<div class="card"><h3>下次调仓</h3>'
               f'<p style="margin:0">月末信号：<b>{e(nr["signal_session"])}</b> 收盘 → 执行：<b>{e(nr["execution_session"])}</b> 开盘{est}</p>'
               f'<p class="note">还有 {nr["sessions_left"]} 个交易日 / {nr["days_left"]} 个自然日。</p></div>')
    rt = v["reference_target"]
    pre = "冻结前，仅供参考" if rt["pre_freeze"] else "冻结后"
    out.append(f'<div class="card"><h3>最新一次月末信号的目标组合 <span class="badge b-mute">{pre}</span></h3>')
    out.append(_weights_table(v, rt["weights"]))
    out.append(f'<p class="note">信号日 {e(rt["signal_session"])} 收盘，{e(rt["execution_session"])} 开盘执行'
               f'（状态：{"已执行" if rt["status"] == "COMPLETE" else "待执行"}）。</p></div>')
    h = v["holdings"]
    if h:
        rows = "".join(
            f'<tr><td>{e(_name(v, r["symbol"]))}</td><td>{pct(r["weight"], 1, False)}</td><td>{e(r["entry_date"])}</td>'
            f'<td>{apct(r["ret_since_entry"])}</td></tr>' for r in h["rows"])
        out.append(f'<div class="card"><h3>当前持仓明细</h3><div class="scroll"><table><tr><th>资产</th><th>当前权重</th>'
                   f'<th>入场日</th><th>入场后收益</th></tr>{rows}</table></div>'
                   f'<p class="note">最近一次调仓：{e(h["rebalance_date"])} 开盘（信号 {e(h["signal_session"])} 收盘），'
                   f'单边换手 {pct(h["last_turnover"], 1, False)}，费用 {money(h["last_fee"])}。</p></div>')
    return "".join(out)


def _source_body(v: dict, alerts_html: str) -> str:
    src = v["sources"]
    kinds = sorted(set(src.values()))
    if not src:
        src_txt = "—"
    elif len(kinds) == 1:
        src_txt = kinds[0]
    else:                                   # mixed: list which symbols came from which source
        src_txt = "；".join(f'{k}：{" ".join(sorted(s for s, x in src.items() if x == k))}' for k in kinds)
    badge = "使用了备用数据源" if v["fallback_used"] else "主数据源"
    out = [f'<p class="note" style="margin-top:0">数据截至 {e(v["as_of"])} 收盘 · 数据源 {e(src_txt)}（{badge}）· '
           f'现金收益 {e(v["rate_source"])} · 冻结日 {e(v["freeze_date"])}。</p>']
    notes = list(v["quality_notes"]) + list(v["fetch_notes"])
    if v.get("postponements"):
        notes.append("存在因停牌而顺延的调仓：" + "；".join(
            f'{p["signal_session"]} 信号→{p["actual_execution"]}' for p in v["postponements"]))
    if notes:
        out.append("<ul>" + "".join(f"<li>{e(n)}</li>" for n in notes) + "</ul>")
    out.append(alerts_html)
    return "".join(out)


def _section_ok(v: dict, alerts_html: str = "") -> str:
    cls, sentence = status_line(v)
    out = [f'<div class="mhead"><h2>{e(v["title"])}</h2><span class="sub">数据截至 {cn_date(v["as_of"])}</span></div>',
           f'<div class="status {cls}">{e(sentence)}</div>', _holdings_block(v)]
    risks = risk_lines(v)
    if risks:
        out.append('<div class="block">' + "".join(f'<p class="risk">{e(r)}</p>' for r in risks) + "</div>")
    out.append(_perf_block(v))
    n = _order_count(v)
    o = v.get("orders")
    if o is not None:
        title = "调仓清单（计算失败）" if o.get("error") else f"调仓清单（{n} 笔）"
        out.append(_details(title, _orders_card(v), open_=_pending(v)))
    out.append(_details("各资产离趋势线多远", _trend_body(v)))
    out.append(_details("月末预览与防守仓位说明", _preview_body(v)))
    out.append(_details("数据来源与说明", _source_body(v, alerts_html)))
    return "".join(out)


def _section_err(v: dict, alerts_html: str = "") -> str:
    cls, sentence = status_line_err(v)
    ls = v.get("last_success")
    detail = (f'<div class="banner err"><b>该市场数据获取或计算失败。</b><br>{e(sanitize(v.get("message", "")))}<br>'
              f'最后一次成功：{e(ls) if ls else "无记录"}</div>' + alerts_html)
    return (f'<div class="mhead"><h2>{e(v["title"])}</h2></div><div class="status {cls}">{e(sentence)}</div>'
            + _details("出错详情", detail))


def render_page(views: dict, generated_at: datetime, last_success: dict | None = None,
                alerts: dict | None = None) -> str:
    gen = generated_at.astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")
    secs = []
    for key in MARKET_ORDER:
        v = views.get(key)
        if v is None:
            continue
        if v["status"] == "ok":
            secs.append(f'<section class="market">{_section_ok(v, _alerts_card(alerts, key))}</section>')
        else:
            v = {**v, "last_success": v.get("last_success") or (last_success or {}).get(key)}
            secs.append(f'<section class="market">{_section_err(v, _alerts_card(alerts, key))}</section>')
    body = (f'<div class="wrap"><header class="top"><h1>量化看板</h1>'
            f'<div class="sub">生成时间 {gen}（北京时间）</div><div class="sub">{NOTE}</div></header>'
            f'<main>{"".join(secs)}</main>'
            f'<footer>本页由本地脚本自动生成，不含任何外部资源。</footer></div>')
    html = (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>量化看板</title>'
            f'<style>{CSS}</style></head><body>{body}</body></html>')
    return sanitize(html)
