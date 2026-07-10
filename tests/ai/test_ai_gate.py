"""The synthetic-only gate: default synthetic, fail-closed on anything else."""

import pytest

from govcon.ai.errors import SyntheticGateError
from govcon.ai.gate import assert_synthetic, data_mode, is_synthetic


def test_default_is_synthetic(monkeypatch):
    monkeypatch.delenv("GOVCON_DATA_MODE", raising=False)
    assert data_mode() == "synthetic" and is_synthetic()
    assert_synthetic()  # does not raise


def test_real_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "real")
    assert not is_synthetic()
    with pytest.raises(SyntheticGateError):
        assert_synthetic()


def test_unknown_value_fails_closed(monkeypatch):
    # any unrecognized value is treated as NOT synthetic (fail-closed)
    monkeypatch.setenv("GOVCON_DATA_MODE", "production")
    with pytest.raises(SyntheticGateError):
        assert_synthetic()


def test_case_and_whitespace_tolerant(monkeypatch):
    monkeypatch.setenv("GOVCON_DATA_MODE", "  Synthetic  ")
    assert is_synthetic()
