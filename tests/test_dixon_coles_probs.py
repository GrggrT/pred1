from decimal import Decimal

from app.services.poisson import match_probs_dixon_coles, match_probs


def test_dixon_coles_sums_to_one():
    p_home, p_draw, p_away = match_probs_dixon_coles(Decimal("1.3"), Decimal("1.1"), rho=Decimal("0.1"), k_max=10)
    total = p_home + p_draw + p_away
    assert abs(total - Decimal("1")) < Decimal("0.01")
    assert all(p >= 0 for p in (p_home, p_draw, p_away))


def test_dixon_coles_rho_zero_matches_poisson():
    lam_h = Decimal("1.4")
    lam_a = Decimal("1.2")
    dc = match_probs_dixon_coles(lam_h, lam_a, rho=Decimal("0.0"), k_max=10)
    pois = match_probs(lam_h, lam_a, k_max=10)
    for p_dc, p_p in zip(dc, pois):
        assert abs(p_dc - p_p) < Decimal("0.01")
