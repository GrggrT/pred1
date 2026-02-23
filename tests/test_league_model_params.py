"""Tests for league_model_params helper functions."""

from decimal import Decimal

from app.services.league_model_params import (
    _clamp_decimal,
    _safe_float,
    _as_dict,
    _outcome_1x2,
)
from app.core.decimalutils import D


class TestClampDecimal:
    def test_within_range(self):
        assert _clamp_decimal(D("1.0"), D("0.5"), D("2.0")) == D("1.0")

    def test_below_min(self):
        assert _clamp_decimal(D("0.1"), D("0.5"), D("2.0")) == D("0.5")

    def test_above_max(self):
        assert _clamp_decimal(D("3.0"), D("0.5"), D("2.0")) == D("2.0")

    def test_at_boundary(self):
        assert _clamp_decimal(D("0.5"), D("0.5"), D("2.0")) == D("0.5")
        assert _clamp_decimal(D("2.0"), D("0.5"), D("2.0")) == D("2.0")


class TestSafeFloat:
    def test_valid_number(self):
        assert _safe_float(3.14) == 3.14

    def test_string_number(self):
        assert _safe_float("2.5") == 2.5

    def test_none_returns_default(self):
        assert _safe_float(None) is None
        assert _safe_float(None, 0.0) == 0.0

    def test_invalid_returns_default(self):
        assert _safe_float("abc", -1.0) == -1.0

    def test_decimal(self):
        assert _safe_float(Decimal("1.23")) == 1.23


class TestAsDict:
    def test_dict_passthrough(self):
        d = {"key": "val"}
        assert _as_dict(d) is d

    def test_json_string(self):
        assert _as_dict('{"a": 1}') == {"a": 1}

    def test_none(self):
        assert _as_dict(None) is None

    def test_invalid_json(self):
        assert _as_dict("not json") is None

    def test_non_dict_json(self):
        assert _as_dict("[1,2,3]") is None


class TestOutcome1x2:
    def test_home_win(self):
        assert _outcome_1x2(3, 1) == "HOME_WIN"

    def test_draw(self):
        assert _outcome_1x2(1, 1) == "DRAW"

    def test_away_win(self):
        assert _outcome_1x2(0, 2) == "AWAY_WIN"

    def test_none_goals(self):
        assert _outcome_1x2(None, 1) is None
        assert _outcome_1x2(1, None) is None
        assert _outcome_1x2(None, None) is None
