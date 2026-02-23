"""Tests for odds utilities (overround removal)."""

from decimal import Decimal

import pytest

from app.services.odds_utils import remove_overround_basic, remove_overround_binary


class TestRemoveOverroundBasic:
    def test_standard_overround(self):
        """Стандартный случай — odds (1.90, 3.50, 4.20), overround ~105%.
        После нормализации сумма ровно 1.0."""
        ph, pd, pa = remove_overround_basic(
            Decimal("1.90"), Decimal("3.50"), Decimal("4.20")
        )
        total = ph + pd + pa
        assert abs(total - Decimal("1")) < Decimal("0.0005")
        # Home probability should be highest (lowest odd)
        assert ph > pd
        assert ph > pa

    def test_fair_odds_no_change(self):
        """Fair odds (2.00, 3.00, 6.00) — implied = 0.5 + 0.333 + 0.167 = 1.0.
        Нормализация не должна существенно менять значения."""
        ph, pd, pa = remove_overround_basic(
            Decimal("2.00"), Decimal("3.00"), Decimal("6.00")
        )
        total = ph + pd + pa
        assert abs(total - Decimal("1")) < Decimal("0.0005")
        assert abs(ph - Decimal("0.5")) < Decimal("0.001")
        assert abs(pd - Decimal("0.3333")) < Decimal("0.001")
        assert abs(pa - Decimal("0.1667")) < Decimal("0.001")

    def test_high_overround(self):
        """High overround ~112% — odds (1.50, 3.80, 6.50)."""
        ph, pd, pa = remove_overround_basic(
            Decimal("1.50"), Decimal("3.80"), Decimal("6.50")
        )
        total = ph + pd + pa
        assert abs(total - Decimal("1")) < Decimal("0.0005")
        assert ph > Decimal("0.5")  # Favourite

    def test_negative_odd_raises(self):
        """ValueError при odd <= 0."""
        with pytest.raises(ValueError):
            remove_overround_basic(Decimal("-1.5"), Decimal("3.50"), Decimal("4.20"))
        with pytest.raises(ValueError):
            remove_overround_basic(Decimal("1.50"), Decimal("0"), Decimal("4.20"))

    def test_none_handling(self):
        """None input — возвращаем (None, None, None)."""
        assert remove_overround_basic(None, Decimal("3.50"), Decimal("4.20")) == (None, None, None)
        assert remove_overround_basic(Decimal("1.90"), None, Decimal("4.20")) == (None, None, None)
        assert remove_overround_basic(None, None, None) == (None, None, None)


class TestRemoveOverroundBinary:
    def test_standard_binary(self):
        """Over/Under с типичным overround."""
        pa, pb = remove_overround_binary(Decimal("1.85"), Decimal("1.95"))
        total = pa + pb
        assert abs(total - Decimal("1")) < Decimal("0.0005")
        # Under (1.95) has lower implied prob than Over (1.85)
        assert pa > pb

    def test_fair_binary(self):
        """Fair binary market — 50/50."""
        pa, pb = remove_overround_binary(Decimal("2.00"), Decimal("2.00"))
        assert abs(pa - Decimal("0.5")) < Decimal("0.001")
        assert abs(pb - Decimal("0.5")) < Decimal("0.001")

    def test_none_handling_binary(self):
        assert remove_overround_binary(None, Decimal("1.95")) == (None, None)
        assert remove_overround_binary(Decimal("1.85"), None) == (None, None)

    def test_negative_odd_raises_binary(self):
        with pytest.raises(ValueError):
            remove_overround_binary(Decimal("0"), Decimal("1.95"))
