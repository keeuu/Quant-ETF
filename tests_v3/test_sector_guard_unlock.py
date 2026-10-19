import json

import pytest

from research_v3.sector_mom import guard


def _write(p, obj):
    p.write_text(json.dumps(obj))
    return p


def test_default_limit_is_dev_end():
    assert guard.current_limit() == guard.DEV_END
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_date_allowed("2024-10-08")


def test_open_holdout_refuses_without_files(tmp_path):
    with pytest.raises(guard.HoldoutAccessError):
        with guard.open_holdout(tmp_path / "missing_auth.json", tmp_path / "missing_state.json"):
            pass
    assert guard.current_limit() == guard.DEV_END


def test_open_holdout_refuses_unauthorized_or_not_started(tmp_path):
    started = _write(tmp_path / "state.json", {"status": "started"})
    bad_auth = _write(tmp_path / "auth_bad.json", {"authorized": "yes"})
    with pytest.raises(guard.HoldoutAccessError):
        with guard.open_holdout(bad_auth, started):
            pass
    auth = _write(tmp_path / "auth.json", {"authorized": True})
    done = _write(tmp_path / "state_done.json", {"status": "completed"})
    with pytest.raises(guard.HoldoutAccessError):
        with guard.open_holdout(auth, done):
            pass
    assert guard.current_limit() == guard.DEV_END


def test_open_holdout_lifts_and_restores_limit(tmp_path):
    auth = _write(tmp_path / "auth.json", {"authorized": True})
    state = _write(tmp_path / "state.json", {"status": "started"})
    with guard.open_holdout(auth, state):
        assert guard.current_limit() == guard.HOLDOUT_END
        guard.assert_date_allowed("2026-09-30")
        with pytest.raises(guard.HoldoutAccessError):
            guard.assert_date_allowed("2026-10-01")
    assert guard.current_limit() == guard.DEV_END
    with pytest.raises(RuntimeError):
        with guard.open_holdout(auth, state):
            raise RuntimeError("boom")
    assert guard.current_limit() == guard.DEV_END
    with pytest.raises(guard.HoldoutAccessError):
        guard.assert_date_allowed("2024-10-01")
