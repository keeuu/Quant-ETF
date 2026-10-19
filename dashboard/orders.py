"""Order list (调仓清单): what to trade to move a real account from the shadow's current holdings to a target.

Pure arithmetic on top of an already computed market view. Nothing here feeds back into signals, NAV or the
shadow replay: compute_orders only reads the view and the latest bars and returns a new dict.
CN: whole lots (default 100 shares), commission max(min_fee, bp of amount) per order, leftover cash routed to the
cash proxy ETF in whole lots. US: fractional shares (Alpaca) plus a whole-share variant, zero commission.
"""
from __future__ import annotations

import math
from typing import Any

DISCLAIMER = "仅为根据冻结规则计算的参考清单，不构成投资建议，下单需自行决定"
EPS = 1e-9
FRAC_DP = 4                      # fractional shares are floored to 4 decimals


class OrderError(ValueError):
    pass


def commission(amount: float, bp: float, min_fee: float) -> float:
    """Per-order commission: max(min_fee, bp basis points of the traded amount); no order, no fee."""
    if amount <= EPS:
        return 0.0
    if bp <= 0 and min_fee <= 0:
        return 0.0
    return max(float(min_fee), amount * bp / 1e4)


def floor_shares(value: float, price: float, lot: float) -> float:
    """Largest holding (in whole `lot`s, or floored to FRAC_DP decimals when lot == 0) worth at most `value`."""
    if value <= 0 or price <= 0:
        return 0.0
    if lot and lot > 0:
        return float(int(math.floor(value / (price * lot) + EPS)) * lot)
    f = 10 ** FRAC_DP
    return math.floor(value / price * f + EPS) / f


def _row(sym, name, price, cur, tgt, bp, min_fee, is_proxy=False):
    d = tgt - cur
    amt = abs(d) * price
    return {"symbol": sym, "name": name, "price": price, "cur_shares": cur, "tgt_shares": tgt, "delta": d,
            "side": "买入" if d > EPS else "卖出" if d < -EPS else "持有", "amount": amt if abs(d) > EPS else 0.0,
            "fee": commission(amt, bp, min_fee) if abs(d) > EPS else 0.0, "is_proxy": is_proxy}


def plan(*, capital: float, cur_w: dict, tgt_w: dict, prices: dict, names: dict, universe, proxy: str,
         proxy_price: float, lot: float, bp: float, min_fee: float, hold_cash_in_proxy: bool = True) -> dict:
    """One order plan. lot > 0: whole lots; lot == 0: fractional. cur_w / tgt_w map symbol -> weight of capital
    (cash weight is implied and routed to the proxy). hold_cash_in_proxy=False means the current cash is plain
    account cash (nothing bought yet) instead of proxy shares."""
    syms = [s for s in universe if cur_w.get(s, 0) > EPS or tgt_w.get(s, 0) > EPS]
    pr = dict(prices)
    pr[proxy] = proxy_price
    missing = [s for s in syms if not pr.get(s) or pr[s] <= 0]
    if missing:
        raise OrderError(f"缺少价格：{', '.join(missing)}")
    cur = {s: floor_shares(capital * cur_w.get(s, 0.0), pr[s], lot) for s in syms}
    cur_cash_w = max(0.0, 1.0 - sum(cur_w.get(s, 0.0) for s in syms))
    cur[proxy] = floor_shares(capital * cur_cash_w, proxy_price, lot) if hold_cash_in_proxy else 0.0
    cur_value = sum(cur[s] * pr[s] for s in cur)
    cash_before = capital - cur_value
    tgt = {s: floor_shares(capital * tgt_w.get(s, 0.0), pr[s], lot) for s in syms}
    notes = [f"{s} 目标金额 {capital * tgt_w[s]:,.0f} 不足一手(一手约 {pr[s] * lot:,.0f})，该仓位并入现金代理"
             for s in syms if tgt_w.get(s, 0) > EPS and tgt[s] <= EPS and lot]

    def evaluate(proxy_sh):
        t = dict(tgt)
        t[proxy] = proxy_sh
        rows = [_row(s, names.get(s, s), pr[s], cur.get(s, 0.0), t.get(s, 0.0), bp, min_fee) for s in syms]
        rows.append(_row(proxy, names.get(proxy, "现金代理"), proxy_price, cur[proxy], proxy_sh, bp, min_fee, True))
        fees = sum(r["fee"] for r in rows)
        buys = sum(r["amount"] for r in rows if r["side"] == "买入")
        sells = sum(r["amount"] for r in rows if r["side"] == "卖出")
        return rows, fees, cash_before + sells - buys - fees

    # route the leftover to the proxy; step down until fees and rounding leave non-negative cash
    while True:
        remaining = capital - sum(tgt[s] * pr[s] for s in syms)
        proxy_sh = floor_shares(remaining, proxy_price, lot)
        rows, fees, cash_after = evaluate(proxy_sh)
        while cash_after < -EPS and proxy_sh > EPS:
            step = lot if lot else 10 ** -FRAC_DP
            proxy_sh = max(0.0, round(proxy_sh - step, 8))
            rows, fees, cash_after = evaluate(proxy_sh)
        if cash_after >= -EPS or not any(tgt[s] > EPS for s in syms):
            break
        big = max((s for s in syms if tgt[s] > EPS), key=lambda s: tgt[s] * pr[s])     # fees exceed cash: trim one lot
        tgt[big] = max(0.0, tgt[big] - (lot or 10 ** -FRAC_DP))
        notes.append(f"费用后现金不足，{big} 少买一手")
    rows.sort(key=lambda r: (r["is_proxy"], r["symbol"]))
    return {"rows": rows, "fees": fees, "cash_before": cash_before, "cash_after": cash_after,
            "turnover_amount": sum(r["amount"] for r in rows), "notes": notes,
            "proxy_lot_cost": proxy_price * lot if lot else None}


def _targets(view: dict) -> list[dict]:
    out = []
    rt = view["reference_target"]
    if rt["status"] != "COMPLETE" and not rt["pre_freeze"]:
        out.append({"kind": "official", "title": "正式调仓清单（信号已产生，待执行）",
                    "target_label": f"月末信号 {rt['signal_session']} 收盘的正式目标，{rt['execution_session']} 开盘执行",
                    "weights": rt["weights"], "signal_session": rt["signal_session"],
                    "execution_session": rt["execution_session"]})
    wi = view["what_if"]
    out.append({"kind": "preview", "title": "月末预览清单（假设，非正式）",
                "target_label": f"假设 {wi['signal_session']} 收盘为月末信号，若成真将于 {wi['execution_session_if']} 开盘执行",
                "weights": wi["weights"], "signal_session": wi["signal_session"],
                "execution_session": wi["execution_session_if"]})
    return out


def compute_orders(view: dict, bars: dict, account: dict, extra_prices: dict | None = None) -> dict:
    """Order list(s) for one market view. Never mutates its inputs."""
    cn = view["key"] == "CN"
    cap = float(account["capital"])
    proxy = str(account["cash_proxy"])
    lot = float(account.get("lot_size", 100 if cn else 1))
    bp, mf = float(account.get("commission_bp", 0)), float(account.get("min_fee", 0))
    extra = extra_prices or {}
    if proxy in extra and extra[proxy] and extra[proxy] > 0:
        pprice, pnote = float(extra[proxy]), f"{proxy} 行情价"
    else:
        pprice, pnote = float(account.get("nominal_proxy_price", 100.0)), f"{proxy} 无行情，使用参考价"
    prices = {s: float(df["close"].iloc[-1]) for s, df in bars.items() if s in view["universe"]}
    names = dict(view["names"])
    names[proxy] = {"511880": "银华日利(货币ETF)", "SGOV": "iShares 0-3月美债", "BIL": "SPDR 1-3月美债"}.get(proxy, "现金代理")
    h = view.get("holdings")
    cur_w = {r["symbol"]: float(r["weight"]) for r in h["rows"]} if h else {}
    base_label = (f"当前持仓（影子组合，自 {h['rebalance_date']} 起）" if h else "当前空仓（首次建仓）")
    sections = []
    for t in _targets(view):
        tw = {k: float(v) for k, v in t["weights"].items()}
        common = dict(capital=cap, cur_w=cur_w, tgt_w=tw, prices=prices, names=names, universe=view["universe"],
                      proxy=proxy, proxy_price=pprice, bp=bp, min_fee=mf, hold_cash_in_proxy=bool(h))
        sec = {**{k: t[k] for k in ("kind", "title", "target_label", "signal_session", "execution_session")},
               "basis_label": base_label}
        if cn:
            p = plan(lot=lot, **common)
            sec.update(p)
        else:
            p = plan(lot=0, **common)
            w = plan(lot=1, **common)
            wmap = {r["symbol"]: r for r in w["rows"]}
            for r in p["rows"]:
                x = wmap.get(r["symbol"])
                r["whole_cur"], r["whole_tgt"], r["whole_delta"] = (x["cur_shares"], x["tgt_shares"], x["delta"]) if x else (0, 0, 0)
            sec.update(p)
            sec["cash_after_whole"] = w["cash_after"]
            sec["notes"] = p["notes"] + w["notes"]
        sections.append(sec)
    return {"market": view["key"], "capital": cap, "currency": account.get("currency", "CNY" if cn else "USD"),
            "cash_proxy": proxy, "proxy_price": pprice, "proxy_note": pnote, "lot": lot if cn else 0,
            "price_date": view["as_of"], "fractional": not cn, "commission_bp": bp, "min_fee": mf,
            "sections": sections, "disclaimer": DISCLAIMER}
