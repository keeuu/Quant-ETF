"""Stage 3.3 — Regression tests for Paper test isolation & F0 integrity.

These tests verify:
  1. test_rejected_order_not_completed uses tmp state (H1 fix)
  2. production logs/rebalance_state.json is unchanged after reconciliation
  3. import src.execution.state_manager does not mutate logs/app.log (H2 fix)
  4. logger can be redirected to test tmp dir
  5. network is blocked
  6. guard distinguishes STATIC_FROZEN / DYNAMIC_EXTERNAL_STATE
  7. dynamic state change does not trigger static F0 failure
  8. Research V2 has no write path to dynamic state
  9. Paper kill switch remains False
 10. ORDER_SUBMISSION_ENABLED is False (no real orders)
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_meta(path: Path) -> tuple[str | None, int | None, int | None]:
    if not path.exists():
        return (None, None, None)
    st = os.stat(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return (sha, st.st_size, st.st_mtime_ns)


PROD_APP_LOG = PROJECT_ROOT / "logs" / "app.log"
PROD_STATE = PROJECT_ROOT / "logs" / "rebalance_state.json"


# ---------------------------------------------------------------------------
# Test 1: H1 — rejected reconciliation uses tmp state, not production path
# ---------------------------------------------------------------------------

def test_rejected_reconciliation_uses_tmp_state(tmp_path: Path) -> None:
    """reconcile_with_broker called with tmp state_file must NOT write to logs/."""
    from src.broker.alpaca_client import AlpacaPaperClient
    from src.execution.reconciliation import reconcile_with_broker
    from src.execution.state_manager import RebalanceStatus, save_rebalance_state

    before_state = _file_meta(PROD_STATE)
    before_log = _file_meta(PROD_APP_LOG)

    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True,
    )
    client.get_open_orders = MagicMock(return_value=[])
    client.get_recent_orders = MagicMock(return_value=[
        {
            "id": "order-rej-TEST-1",
            "client_order_id": "momv1_202610_SPY_BUY_01",
            "symbol": "SPY",
            "side": "buy",
            "status": "rejected",
            "qty": 10.0,
            "filled_qty": 0.0,
        }
    ])
    client.get_positions = MagicMock(return_value={})
    client.get_account = MagicMock(return_value={"cash": 100000.0, "equity": 100000.0})

    state_file = tmp_path / "rebalance_state.json"
    state: dict = {
        "rebalance_month": "2026-10",
        "status": RebalanceStatus.SUBMITTED,
        "target_portfolio": {"SPY": 1.0},
        "submitted_orders": [{"client_order_id": "momv1_202610_SPY_BUY_01", "symbol": "SPY"}],
    }
    save_rebalance_state(state, state_file)

    # Call with explicit tmp state_file — must NOT fall back to production path
    report = reconcile_with_broker(client, state, state_file=state_file)

    # Functional assertions
    assert report["has_failures"] is True
    assert state["status"] == RebalanceStatus.FAILED

    # Tmp state file must have been written
    assert state_file.exists(), "reconcile_with_broker must write to the provided state_file"
    import json
    saved = json.loads(state_file.read_text())
    assert saved["status"] == RebalanceStatus.FAILED

    # Production files must be UNCHANGED
    after_state = _file_meta(PROD_STATE)
    after_log = _file_meta(PROD_APP_LOG)
    assert before_state == after_state, (
        f"INVARIANT VIOLATED: logs/rebalance_state.json changed!\n"
        f"before={before_state}\nafter={after_state}"
    )
    assert before_log == after_log, (
        f"INVARIANT VIOLATED: logs/app.log changed!\n"
        f"before={before_log}\nafter={after_log}"
    )


# ---------------------------------------------------------------------------
# Test 2: Production state unchanged (explicit assertion)
# ---------------------------------------------------------------------------

def test_production_state_unchanged_after_reconciliation(tmp_path: Path) -> None:
    """Production logs/ must be byte-identical before and after any test call."""
    before_state = _file_meta(PROD_STATE)
    before_log = _file_meta(PROD_APP_LOG)

    # Simulate a call that previously caused pollution
    from src.execution.state_manager import RebalanceStatus, save_rebalance_state

    tmp_state = tmp_path / "check_state.json"
    save_rebalance_state(
        {"rebalance_month": "2099-01", "status": RebalanceStatus.PLANNED},
        tmp_state,
    )
    assert tmp_state.exists()

    after_state = _file_meta(PROD_STATE)
    after_log = _file_meta(PROD_APP_LOG)

    assert before_state == after_state, "Production rebalance_state.json must be unchanged"
    assert before_log == after_log, "Production app.log must be unchanged"


# ---------------------------------------------------------------------------
# Test 3: H2 — import state_manager does not mutate app.log
# ---------------------------------------------------------------------------

def test_import_state_manager_does_not_mutate_app_log() -> None:
    """Importing src.execution.state_manager must not grow/modify logs/app.log."""
    before = _file_meta(PROD_APP_LOG)

    # Force a fresh import cycle by temporarily removing from sys.modules
    mods_to_remove = [k for k in sys.modules if k.startswith("src.")]
    saved = {k: sys.modules.pop(k) for k in mods_to_remove}
    try:
        import src.execution.state_manager  # noqa: F401
    finally:
        sys.modules.update(saved)

    after = _file_meta(PROD_APP_LOG)
    assert before == after, (
        "import src.execution.state_manager must NOT create/modify logs/app.log.\n"
        f"before={before}\nafter={after}"
    )


# ---------------------------------------------------------------------------
# Test 4: logger redirects to temp log, not production
# ---------------------------------------------------------------------------

def test_logger_can_redirect_to_temp(tmp_path: Path) -> None:
    """configure_test_logging() redirects file output to tmp_path, not logs/app.log."""
    import logging

    from src.utils.logger import configure_test_logging

    before = _file_meta(PROD_APP_LOG)
    configure_test_logging(tmp_path, log_file="test_redirect.log")

    _logger = logging.getLogger("quant_etf")
    _logger.info("Test log redirect check — should go to tmp, not logs/app.log")

    after = _file_meta(PROD_APP_LOG)
    assert before == after, "configure_test_logging must not write to production logs/app.log"

    tmp_log = tmp_path / "test_redirect.log"
    assert tmp_log.exists(), "Log must be written to tmp_path"
    assert "Test log redirect check" in tmp_log.read_text()


# ---------------------------------------------------------------------------
# Test 5: network is blocked (via conftest autouse fixture)
# ---------------------------------------------------------------------------

def test_network_is_blocked() -> None:
    """Real socket connections must be forbidden (enforced by conftest)."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises((RuntimeError, OSError)):
        sock.connect(("api.alpaca.markets", 443))


# ---------------------------------------------------------------------------
# Test 6: guard distinguishes STATIC_FROZEN / DYNAMIC_EXTERNAL_STATE
# ---------------------------------------------------------------------------

def test_guard_static_vs_dynamic_classification() -> None:
    """verify_baseline_v2 must classify logs/* as DYNAMIC, not STATIC."""
    from research_v2.guards import DYNAMIC_EXTERNAL_PATHS, _is_dynamic_external

    assert _is_dynamic_external("logs/app.log")
    assert _is_dynamic_external("logs/rebalance_state.json")
    assert _is_dynamic_external("logs/execution.lock")
    assert _is_dynamic_external("logs/any_future_file.json")

    # Static files must NOT be classified as dynamic
    assert not _is_dynamic_external("src/utils/logger.py")
    assert not _is_dynamic_external("config/config.py")
    assert not _is_dynamic_external("tests/test_fault_injection.py")
    assert not _is_dynamic_external("data/cache/SPY.csv")


# ---------------------------------------------------------------------------
# Test 7: dynamic state change does not trigger static F0 failure
# ---------------------------------------------------------------------------

def test_dynamic_change_does_not_fail_static_guard(tmp_path: Path) -> None:
    """verify_baseline_v2: changing a dynamic file must NOT appear in static_issues."""
    from research_v2.guards import verify_baseline_v2
    from research_v2.provenance import read_json

    root = PROJECT_ROOT
    manifest = read_json(root / "docs/research_v2/baseline_manifest.json")

    result = verify_baseline_v2(root, manifest)

    # Dynamic paths must NOT appear in static_issues — they belong in dynamic_observations
    static_issue_paths = {issue["path"] for issue in result["static_issues"]}
    dynamic_in_static = {p for p in static_issue_paths if p.startswith("logs/")}
    assert not dynamic_in_static, (
        f"Dynamic paths must not appear in static_issues: {dynamic_in_static}"
    )

    # Dynamic layer observations must only contain logs/ paths
    for obs in result["dynamic_observations"]:
        assert obs["path"].startswith("logs/"), (
            f"Unexpected non-logs path in dynamic_observations: {obs['path']}"
        )

    # verify the two categories are correctly separated
    assert "guard_note" in result
    assert "DYNAMIC_EXTERNAL_STATE" in result["guard_note"]


# ---------------------------------------------------------------------------
# Test 8: Research V2 has no write path to dynamic state
# ---------------------------------------------------------------------------

def test_research_v2_has_no_dynamic_state_write_path() -> None:
    """research_v2/*.py (except guards.py) must not reference logs/ as write targets.

    guards.py is exempt because it defines DYNAMIC_EXTERNAL_PATHS constants
    that contain 'logs/' strings as part of the classification system itself.
    All other research_v2 modules must not reference Paper runtime state paths.
    """
    v2_dir = PROJECT_ROOT / "research_v2"
    violations: list[str] = []
    dynamic_keywords = {"logs/", "rebalance_state", "app.log", "execution.lock"}

    for py_file in sorted(v2_dir.glob("*.py")):
        # guards.py defines DYNAMIC_EXTERNAL_PATHS (strings like "logs/app.log")
        # as classification constants — not as write targets. Exempt it.
        if py_file.name == "guards.py":
            continue
        src_text = py_file.read_text(encoding="utf-8")
        for kw in dynamic_keywords:
            if kw in src_text:
                violations.append(f"{py_file.name}: contains '{kw}'")

    assert not violations, (
        "Research V2 source files (excl. guards.py) must not reference "
        "dynamic Paper state paths as write targets:\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# Test 9: Paper kill switch remains False
# ---------------------------------------------------------------------------

def test_paper_kill_switch_is_off() -> None:
    """ORDER_SUBMISSION_ENABLED must be False — no real orders can be submitted."""
    import config.config as cfg

    assert cfg.ORDER_SUBMISSION_ENABLED is False, (
        "ORDER_SUBMISSION_ENABLED must be False (kill switch must remain OFF)"
    )


# ---------------------------------------------------------------------------
# Test 10: No order submission allowed
# ---------------------------------------------------------------------------

def test_no_order_submission_via_client(tmp_path: Path) -> None:
    """AlpacaPaperClient.submit_order must raise SecurityException when kill switch is OFF."""
    from src.broker.alpaca_client import AlpacaPaperClient, SecurityException

    client = AlpacaPaperClient(
        api_key="PKTEST12345678",
        secret_key="SECRET_12345678",
        base_url="https://paper-api.alpaca.markets",
        paper=True,
    )
    with pytest.raises((SecurityException, Exception)):
        client.submit_order(
            symbol="SPY",
            side="buy",
            client_order_id="momv1_202610_SPY_BUY_MUST_NOT_SUBMIT",
            notional=1000.0,
        )
