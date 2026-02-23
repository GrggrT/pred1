"""Sanity tests: are predictions reasonable?"""

import math
from decimal import Decimal

import pytest

from app.core.decimalutils import D
from app.services.poisson import match_probs, match_probs_dixon_coles
from app.services.dixon_coles import (
    MatchData,
    fit_dixon_coles,
    predict_lambda_mu,
    tau_value,
)


class TestProbsSumToOne:
    def test_standard_poisson_sums_to_one(self):
        p_h, p_d, p_a = match_probs(D("1.5"), D("1.2"))
        total = p_h + p_d + p_a
        assert abs(total - D("1")) < D("0.001")

    def test_dixon_coles_sums_to_one(self):
        p_h, p_d, p_a = match_probs_dixon_coles(D("1.5"), D("1.2"), D("-0.1"))
        total = p_h + p_d + p_a
        assert abs(total - D("1")) < D("0.001")

    def test_extreme_lambdas_sum_to_one(self):
        for lam_h, lam_a in [(D("0.3"), D("0.3")), (D("3.5"), D("0.5")), (D("0.1"), D("4.0"))]:
            p_h, p_d, p_a = match_probs(lam_h, lam_a)
            total = p_h + p_d + p_a
            assert abs(total - D("1")) < D("0.001"), f"Failed for λ={lam_h},{lam_a}"


class TestStrongFavorite:
    def test_strong_home_favorite(self):
        """λ_home=2.5, λ_away=0.8 → p_home > p_away."""
        p_h, p_d, p_a = match_probs(D("2.5"), D("0.8"))
        assert p_h > p_a, f"p_h={p_h}, p_a={p_a}"
        assert p_h > D("0.5"), f"Strong favorite should have >50%: p_h={p_h}"

    def test_strong_away_favorite(self):
        """λ_home=0.8, λ_away=2.5 → p_away > p_home."""
        p_h, p_d, p_a = match_probs(D("0.8"), D("2.5"))
        assert p_a > p_h

    def test_dc_preserves_favorite(self):
        """DC correction shouldn't flip the favorite."""
        p_h, p_d, p_a = match_probs_dixon_coles(D("2.5"), D("0.8"), D("-0.1"))
        assert p_h > p_a


class TestDrawReasonable:
    def test_equal_teams_draw_significant(self):
        """Equal teams at home → draw probability should be >20%."""
        p_h, p_d, p_a = match_probs(D("1.3"), D("1.3"))
        assert p_d > D("0.20"), f"Draw too low for equal teams: p_d={p_d}"
        assert p_d < D("0.40"), f"Draw too high for equal teams: p_d={p_d}"

    def test_draw_between_home_and_away_for_equal(self):
        """For equal lambdas, draw is sandwiched."""
        p_h, p_d, p_a = match_probs(D("1.3"), D("1.3"))
        assert p_h > p_d or p_a > p_d  # Draw shouldn't be the most likely


class TestLambdaBounds:
    def test_reasonable_lambda_range(self):
        """DC predict_lambda_mu should produce λ,μ in [0.1, 5.0] for typical params."""
        # Simulate typical attack/defense values
        att_h, def_h = 0.2, -0.1
        att_a, def_a = 0.1, 0.0
        ha = 0.3

        lam, mu = predict_lambda_mu(att_h, def_h, att_a, def_a, ha)
        assert 0.1 <= lam <= 5.0, f"λ out of range: {lam}"
        assert 0.1 <= mu <= 5.0, f"μ out of range: {mu}"

    def test_extreme_params_still_bounded(self):
        """Even with extreme att/def, lambdas shouldn't explode."""
        att_h, def_h = 0.8, -0.5
        att_a, def_a = -0.3, 0.4
        ha = 0.5

        lam, mu = predict_lambda_mu(att_h, def_h, att_a, def_a, ha)
        assert lam > 0, f"λ must be positive: {lam}"
        assert mu > 0, f"μ must be positive: {mu}"


class TestDCSumToZero:
    def test_fitted_params_sum_to_zero(self):
        """After fitting DC, attack and defense params should sum to ~0."""
        from datetime import date, timedelta

        base = date(2025, 1, 1)
        matches = [
            MatchData(home_id=i % 4, away_id=(i + 1) % 4,
                      home_goals=(i * 7 + 3) % 4, away_goals=(i * 3 + 1) % 3,
                      date=base + timedelta(days=i))
            for i in range(40)
        ]
        params = fit_dixon_coles(matches, ref_date=date(2025, 3, 15))

        att_sum = sum(params.attack.values())
        def_sum = sum(params.defense.values())
        assert abs(att_sum) < 0.05, f"Attack sum not ~0: {att_sum}"
        assert abs(def_sum) < 0.05, f"Defense sum not ~0: {def_sum}"
