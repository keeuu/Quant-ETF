"""Read-only snapshot of the paper account for the dashboard section (presentation only)."""
from __future__ import annotations

from dashboard.safety import sanitize

from . import journal as jr


def last_execution(jpath=None) -> dict | None:
    done = [e for e in jr.read(jpath) if e.get("event") == "complete"]
    return {"signal_date": done[-1]["signal_date"], "ts": done[-1]["ts"]} if done else None


def fetch_snapshot(broker=None, universe=None, proxy: str = "SGOV", jpath=None) -> dict:
    """Never raises: failures come back as {'error': ...}. Contains no account number."""
    try:
        from .broker import PaperBroker
        broker = broker or PaperBroker(allow_submit=False)
        acct = broker.get_account()
        eq = float(acct["equity"])
        strat = set(universe or ()) | {proxy}
        rows = []
        for p in broker.get_positions():
            mv = float(p["market_value"])
            rows.append({"symbol": p["symbol"], "qty": float(p["qty"]), "market_value": mv,
                         "weight": mv / eq if eq else 0.0, "strategy": (not strat) or p["symbol"] in strat})
        rows.sort(key=lambda r: -r["market_value"])
        return {"equity": eq, "cash": float(acct["cash"]), "buying_power": float(acct["buying_power"]),
                "positions": rows, "last_execution": last_execution(jpath)}
    except Exception as e:  # noqa: BLE001
        return {"error": sanitize(f"{type(e).__name__}: {e}")}
