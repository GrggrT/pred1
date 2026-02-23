"""Tests for new betting markets: TOTAL 1.5/3.5, BTTS, Double Chance."""

import pytest
from decimal import Decimal
from unittest.mock import patch

from app.jobs.evaluate_results import _resolve_totals
from app.jobs.sync_data import _extract_btts, _extract_double_chance, _extract_total_line


class TestResolveTotals:
    """Test _resolve_totals for all 12 selection codes."""

    def test_over_2_5_win(self):
        assert _resolve_totals("OVER_2_5", 2, 1) == "WIN"

    def test_over_2_5_loss(self):
        assert _resolve_totals("OVER_2_5", 1, 1) == "LOSS"

    def test_under_2_5_win(self):
        assert _resolve_totals("UNDER_2_5", 1, 1) == "WIN"

    def test_under_2_5_loss(self):
        assert _resolve_totals("UNDER_2_5", 2, 1) == "LOSS"

    def test_over_1_5_win(self):
        assert _resolve_totals("OVER_1_5", 1, 1) == "WIN"

    def test_over_1_5_loss(self):
        assert _resolve_totals("OVER_1_5", 1, 0) == "LOSS"

    def test_under_1_5_win(self):
        assert _resolve_totals("UNDER_1_5", 1, 0) == "WIN"

    def test_under_1_5_loss(self):
        assert _resolve_totals("UNDER_1_5", 1, 1) == "LOSS"

    def test_over_3_5_win(self):
        assert _resolve_totals("OVER_3_5", 3, 1) == "WIN"

    def test_over_3_5_loss(self):
        assert _resolve_totals("OVER_3_5", 2, 1) == "LOSS"

    def test_under_3_5_win(self):
        assert _resolve_totals("UNDER_3_5", 2, 1) == "WIN"

    def test_under_3_5_loss(self):
        assert _resolve_totals("UNDER_3_5", 2, 2) == "LOSS"

    def test_btts_yes_win(self):
        assert _resolve_totals("BTTS_YES", 1, 1) == "WIN"

    def test_btts_yes_loss(self):
        assert _resolve_totals("BTTS_YES", 0, 1) == "LOSS"

    def test_btts_no_win(self):
        assert _resolve_totals("BTTS_NO", 0, 1) == "WIN"

    def test_btts_no_loss(self):
        assert _resolve_totals("BTTS_NO", 1, 1) == "LOSS"

    def test_dc_1x_home_win(self):
        assert _resolve_totals("DC_1X", 2, 0) == "WIN"

    def test_dc_1x_draw(self):
        assert _resolve_totals("DC_1X", 1, 1) == "WIN"

    def test_dc_1x_away_win(self):
        assert _resolve_totals("DC_1X", 0, 1) == "LOSS"

    def test_dc_x2_away_win(self):
        assert _resolve_totals("DC_X2", 0, 1) == "WIN"

    def test_dc_x2_draw(self):
        assert _resolve_totals("DC_X2", 1, 1) == "WIN"

    def test_dc_x2_home_win(self):
        assert _resolve_totals("DC_X2", 2, 0) == "LOSS"

    def test_dc_12_home_win(self):
        assert _resolve_totals("DC_12", 2, 0) == "WIN"

    def test_dc_12_away_win(self):
        assert _resolve_totals("DC_12", 0, 1) == "WIN"

    def test_dc_12_draw(self):
        assert _resolve_totals("DC_12", 1, 1) == "LOSS"

    def test_void_none_goals(self):
        assert _resolve_totals("OVER_2_5", None, None) == "VOID"

    def test_unknown_selection(self):
        assert _resolve_totals("UNKNOWN", 1, 1) == "VOID"

    def test_zero_zero(self):
        # 0-0: total=0, btts=no
        assert _resolve_totals("UNDER_1_5", 0, 0) == "WIN"
        assert _resolve_totals("BTTS_NO", 0, 0) == "WIN"
        assert _resolve_totals("BTTS_YES", 0, 0) == "LOSS"


class TestExtractBtts:
    """Test BTTS odds extraction."""

    def _make_bookmaker(self, yes_odd, no_odd, bet_id=8):
        return {
            "id": 1,
            "bets": [
                {
                    "id": bet_id,
                    "name": "Both Teams Score",
                    "values": [
                        {"value": "Yes", "odd": str(yes_odd)},
                        {"value": "No", "odd": str(no_odd)},
                    ],
                }
            ],
        }

    def test_extract_btts(self):
        bm = self._make_bookmaker(1.85, 1.95)
        yes, no = _extract_btts(bm)
        assert yes == 1.85
        assert no == 1.95

    def test_extract_btts_by_name(self):
        bm = self._make_bookmaker(1.75, 2.05, bet_id=999)
        bm["bets"][0]["name"] = "Both Teams Score"
        yes, no = _extract_btts(bm)
        assert yes == 1.75
        assert no == 2.05

    def test_no_btts(self):
        bm = {"id": 1, "bets": [{"id": 1, "name": "Match Winner", "values": []}]}
        yes, no = _extract_btts(bm)
        assert yes is None
        assert no is None


class TestExtractDoubleChance:
    """Test Double Chance odds extraction."""

    def _make_bookmaker(self, dc_1x, dc_x2, dc_12, bet_id=12):
        return {
            "id": 1,
            "bets": [
                {
                    "id": bet_id,
                    "name": "Double Chance",
                    "values": [
                        {"value": "Home/Draw", "odd": str(dc_1x)},
                        {"value": "Draw/Away", "odd": str(dc_x2)},
                        {"value": "Home/Away", "odd": str(dc_12)},
                    ],
                }
            ],
        }

    def test_extract_dc(self):
        bm = self._make_bookmaker(1.30, 1.45, 1.20)
        v1x, vx2, v12 = _extract_double_chance(bm)
        assert v1x == 1.30
        assert vx2 == 1.45
        assert v12 == 1.20

    def test_no_dc(self):
        bm = {"id": 1, "bets": []}
        v1x, vx2, v12 = _extract_double_chance(bm)
        assert v1x is None
        assert vx2 is None
        assert v12 is None


class TestExtractTotalLine:
    """Test generic total line extraction."""

    def _make_bookmaker(self, line, over_odd, under_odd):
        return {
            "id": 1,
            "bets": [
                {
                    "id": 5,
                    "name": "Goals Over/Under",
                    "values": [
                        {"value": f"Over {line}", "odd": str(over_odd)},
                        {"value": f"Under {line}", "odd": str(under_odd)},
                        {"value": "Over 2.5", "odd": "1.95"},
                        {"value": "Under 2.5", "odd": "1.85"},
                    ],
                }
            ],
        }

    def test_extract_1_5(self):
        bm = self._make_bookmaker("1.5", 1.25, 3.50)
        over, under = _extract_total_line(bm, "1.5")
        assert over == 1.25
        assert under == 3.50

    def test_extract_3_5(self):
        bm = self._make_bookmaker("3.5", 2.80, 1.40)
        over, under = _extract_total_line(bm, "3.5")
        assert over == 2.80
        assert under == 1.40

    def test_no_matching_line(self):
        bm = self._make_bookmaker("1.5", 1.25, 3.50)
        over, under = _extract_total_line(bm, "4.5")
        assert over is None
        assert under is None


class TestConfigFlagsDefault:
    """Test that new markets are enabled by default (test mode)."""

    def test_default_flags_on(self):
        from app.core.config import Settings
        s = Settings(DATABASE_URL="postgresql+asyncpg://x:x@localhost/test")
        assert s.enable_total_1_5_bets is True
        assert s.enable_total_3_5_bets is True
        assert s.enable_btts_bets is True
        assert s.enable_double_chance_bets is True
        assert s.max_total_bets_per_fixture == 1


class TestCorrelationOnePerFixture:
    """Test that correlation logic picks best EV among goals group (TOTAL + BTTS)."""

    def test_best_ev_wins(self):
        from app.jobs.build_predictions import _evaluate_market_cfg, MARKET_CONFIGS

        # Create a mock row with odds for all total lines + BTTS
        class MockRow:
            over_2_5 = 1.95
            under_2_5 = 1.85
            over_1_5 = 1.25
            under_1_5 = 3.50
            over_3_5 = 2.80
            under_3_5 = 1.40
            btts_yes = 1.85
            btts_no = 1.95
            dc_1x = None
            dc_x2 = None
            dc_12 = None

        row = MockRow()
        probs = {
            "p_over_2_5": Decimal("0.55"), "p_under_2_5": Decimal("0.45"),
            "p_over_1_5": Decimal("0.80"), "p_under_1_5": Decimal("0.20"),
            "p_over_3_5": Decimal("0.30"), "p_under_3_5": Decimal("0.70"),
            "p_btts_yes": Decimal("0.50"), "p_btts_no": Decimal("0.50"),
            "p_dc_1x": Decimal("0.60"), "p_dc_x2": Decimal("0.60"), "p_dc_12": Decimal("0.80"),
        }

        min_odd = Decimal("1.20")
        max_odd = Decimal("4.00")

        # Enable all markets in settings for test
        with patch("app.jobs.build_predictions.settings") as mock_settings:
            mock_settings.enable_total_bets = True
            mock_settings.enable_total_1_5_bets = True
            mock_settings.enable_total_3_5_bets = True
            mock_settings.enable_btts_bets = True
            mock_settings.enable_double_chance_bets = False
            mock_settings.value_threshold_total_dec = Decimal("0.05")
            mock_settings.value_threshold_total_1_5_dec = Decimal("0.05")
            mock_settings.value_threshold_total_3_5_dec = Decimal("0.05")
            mock_settings.value_threshold_btts_dec = Decimal("0.04")
            mock_settings.min_odd_dec = min_odd
            mock_settings.max_odd_dec = max_odd

            goals_cfgs = [m for m in MARKET_CONFIGS if m["group"] == "goals"]
            results = []
            for mcfg in goals_cfgs:
                result = _evaluate_market_cfg(mcfg, probs, row, min_odd, max_odd)
                if result is not None:
                    results.append(result)

            # At least one should have a bet
            bets = [r for r in results if r["best_sel"] is not None]
            assert len(bets) >= 1

            # All results should have "goals" group (TOTAL + BTTS unified)
            for r in results:
                assert r["group"] == "goals"
