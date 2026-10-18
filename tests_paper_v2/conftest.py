import os
import tempfile

# Must run before paper_v2.config is imported: keep tests away from the real paper_v2_state/.
os.environ["PAPER_V2_STATE_DIR"] = tempfile.mkdtemp(prefix="paper_v2_test_state_")

import pandas as pd  # noqa: E402
import pytest  # noqa: E402


from paper_v2 import irx as _irx  # noqa: E402

REAL_DOWNLOAD_FORWARD = _irx._download_forward


def fake_forward_irx(as_of):
    """Deterministic stand-in for the yfinance ^IRX download (business days from 2021-01-01)."""
    idx = pd.bdate_range("2021-01-01", as_of).strftime("%Y-%m-%d")
    return pd.Series([0.05 + 0.0001 * (i % 7) for i in range(len(idx))], index=idx, dtype=float)


@pytest.fixture(autouse=True)
def no_network_irx(monkeypatch):
    """Tests never touch the network for ^IRX (nor for anything else in paper_v2)."""
    from paper_v2 import irx
    monkeypatch.setattr(irx, "_download_forward", fake_forward_irx)
