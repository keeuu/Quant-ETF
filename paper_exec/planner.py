"""Pure order arithmetic: current paper positions + target weights -> sells first, then buys."""
from __future__ import annotations

import math

EPS = 1e-9


def client_order_id(signal_date: str, symbol: str, side: str) -> str:
    return f"capdef-{signal_date}-{symbol}-{side}"


def target_weights(weights: dict, universe, proxy: str) -> dict:
    """Strategy weights -> per-symbol weights; the CASH slot goes to the cash proxy (SGOV)."""
    tw = {s: float(weights.get(s, 0.0)) for s in universe}
    tw[proxy] = tw.get(proxy, 0.0) + float(weights.get("CASH", 0.0))
    return {s: w for s, w in tw.items() if w > EPS}


def compute_plan(*, signal_date: str, weights: dict, universe, proxy: str, equity: float, positions: dict,
                 fractionable: dict, prices: dict | None = None, min_usd: float = 1.0) -> dict:
    """positions: symbol -> {qty, market_value, side?, price?}. Non-strategy symbols are never traded."""
    strat = set(universe) | {proxy}
    tw = target_weights(weights, universe, proxy)
    other = {s: p for s, p in positions.items() if s not in strat}
    other_value = sum(float(p["market_value"]) for p in other.values())
    base = float(equity) - other_value                    # money the strategy is responsible for
    prices = dict(prices or {})
    orders, notes = [], []
    for sym in sorted(strat):
        p = positions.get(sym)
        if p and str(p.get("side", "long")).lower() == "short":
            notes.append(f"{sym} 为空头持仓，不处理")
            continue
        cur = float(p["market_value"]) if p else 0.0
        tgt = base * tw.get(sym, 0.0)
        delta = tgt - cur
        if abs(delta) < min_usd:
            continue
        frac = bool(fractionable.get(sym, False))
        px = prices.get(sym) or (float(p["price"]) if p and p.get("price") else None)
        o = {"symbol": sym, "est_usd": round(abs(delta), 2)}
        if delta < 0:
            o["side"] = "sell"
            liquidate = tw.get(sym, 0.0) <= EPS or tgt < min_usd
            if liquidate and p:
                o["qty"] = str(p["qty"])
                o["est_usd"] = round(cur, 2)
            elif frac:
                o["notional"] = round(min(-delta, cur), 2)
            elif px:
                q = math.floor(-delta / px)
                if q < 1:
                    notes.append(f"{sym} 不可碎股且卖出金额不足 1 股，跳过")
                    continue
                o["qty"] = str(q)
            else:
                notes.append(f"{sym} 不可碎股且无价格，跳过")
                continue
        else:
            o["side"] = "buy"
            if frac:
                o["notional"] = round(delta, 2)
            elif px:
                q = math.floor(delta / px)
                if q < 1:
                    notes.append(f"{sym} 不可碎股且买入金额不足 1 股，跳过")
                    continue
                o["qty"] = str(q)
                o["est_usd"] = round(q * px, 2)
            else:
                notes.append(f"{sym} 不可碎股且无价格，跳过")
                continue
        o["client_order_id"] = client_order_id(signal_date, sym, o["side"])
        orders.append(o)
    orders.sort(key=lambda o: (o["side"] != "sell", o["symbol"]))            # sells first
    return {"signal_date": signal_date, "equity": float(equity), "strategy_base": base,
            "non_strategy": {s: float(p["market_value"]) for s, p in other.items()},
            "target_weights": tw, "orders": orders, "notes": notes}
