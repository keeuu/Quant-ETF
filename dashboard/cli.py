"""`python -m dashboard run`: fetch, replay, render. One failing market never blocks the other."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .safety import load_env, sanitize, setup_logging

log = logging.getLogger("dashboard")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _read_last_success(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def default_markets():
    """market key -> (spec factory, fetch function, base calendar factory)."""
    from research_v2.calendar import get_trading_calendar
    from research_v3.cn_transfer.calendar_cn import get_xshg_calendar
    from . import fetch
    return {"US": (config.us_spec, fetch.fetch_us, get_trading_calendar),
            "CN": (config.cn_spec, fetch.fetch_cn, get_xshg_calendar)}


def run(*, now_utc: datetime | None = None, output_dir: Path | None = None, data_root: Path | None = None,
        snapshot_dir: Path | None = None, markets: dict | None = None, freeze_date: str | None = None,
        last_success_path: Path | None = None, write_snapshots: bool = True, notify: bool = False,
        notifier=None, alerts_path: Path | None = None, alert_state_path: Path | None = None) -> int:
    """Returns the process exit code: 1 only if every market failed."""
    from . import fetch
    from .render import render_page
    from .shadow import compute_market_view

    now_utc = now_utc or datetime.now(timezone.utc)
    output_dir = Path(output_dir or config.OUTPUT_DIR)
    data_root = Path(data_root or config.DATA_ROOT)
    last_path = Path(last_success_path or (data_root / "last_success.json"))
    freeze_date = freeze_date or config.frozen_spec()["freeze_date"]
    markets = markets or default_markets()
    run_date = now_utc.astimezone(config.SHANGHAI).strftime("%Y-%m-%d")
    last_success = _read_last_success(last_path)
    views: dict = {}

    for key, (spec_f, fetch_f, cal_f) in markets.items():
        try:
            spec = spec_f()
            if snapshot_dir:
                fr = fetch.load_snapshot(snapshot_dir, key)
                run_now = datetime.fromisoformat(fr.fetched_at_utc)
            else:
                kw = {"data_root": data_root} if fetch_f is fetch.fetch_us else {}   # cached snapshots for the fallback
                fr, run_now = fetch_f(spec, now_utc, **kw), now_utc
                if write_snapshots:
                    fetch.write_snapshot(data_root, run_date, fr)
            meta = {"sources": fr.sources, "rate_source": fr.rate_source, "notes": fr.notes,
                    "fallback_used": fr.fallback_used, "fetched_at_utc": fr.fetched_at_utc,
                    "extra_prices": fr.extra_prices}
            view = compute_market_view(spec, fr.bars, fr.rate, run_now, base_cal=cal_f(), freeze_date=freeze_date,
                                       meta=meta, account=config.load_account().get(key))
            views[key] = view
            last_success[key] = view["as_of"]
            log.info("%s ok: as_of=%s sources=%s fallback=%s window_sessions=%d", key, view["as_of"],
                     sorted(set(fr.sources.values())), fr.fallback_used, view["window"]["n_sessions"])
        except Exception as e:
            msg = sanitize(f"{type(e).__name__}: {e}")
            log.error("%s FAILED: %s\n%s", key, msg, sanitize(traceback.format_exc()))
            title = {"US": "美股", "CN": "A股"}.get(key, key)
            views[key] = {"key": key, "title": title, "status": "error", "message": msg,
                          "last_success": last_success.get(key)}

    ok = [k for k, v in views.items() if v["status"] == "ok"]
    alert_info = None
    try:                                  # alerts are read-only and must never stop the page from rendering
        from . import alerts
        alert_info = alerts.process(
            views, now_utc, cfg=alerts.load_alert_config(alerts_path),
            state_path=Path(alert_state_path or (data_root / "alert_state.json")),
            notify=notify and not snapshot_dir, notifier=notifier)
    except Exception as e:  # noqa: BLE001
        log.error("alerts failed: %s", sanitize(f"{type(e).__name__}: {e}"))
    html = render_page(views, now_utc, last_success, alerts=alert_info)
    _atomic_write(output_dir / "index.html", html)
    _atomic_write(output_dir / "archive" / f"{run_date}.html", html)
    if ok and not snapshot_dir:
        _atomic_write(last_path, json.dumps(last_success, indent=2, sort_keys=True))
    log.info("rendered %s (markets ok: %s)", output_dir / "index.html", ok or "none")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dashboard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fetch data, replay the frozen shadow, render the HTML dashboard")
    r.add_argument("--snapshot", type=Path, help="re-render from a saved dashboard/data/<date>/ snapshot (no network)")
    r.add_argument("--out", type=Path, help="output directory (default dashboard/output)")
    r.add_argument("--no-notify", action="store_true", help="evaluate and show alerts but send no macOS notification "
                   "(and do not mark any alert as notified)")
    args = ap.parse_args(argv)
    load_env(config.REPO)
    setup_logging(config.LOG_PATH)
    try:
        out = args.out or (config.OUTPUT_DIR / f"replay_{args.snapshot.name}" if args.snapshot else None)
        return run(snapshot_dir=args.snapshot, output_dir=out, notify=not args.no_notify)
    except Exception as e:  # never leak secrets, never traceback to a terminal without sanitizing
        log.error("fatal: %s", sanitize(f"{type(e).__name__}: {e}"))
        return 1
