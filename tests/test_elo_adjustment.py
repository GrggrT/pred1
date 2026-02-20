from decimal import Decimal

from app.jobs.build_predictions import elo_adjust_factor, LAMBDA_EPS
from app.core.decimalutils import D, q_money


def test_elo_adjust_factor_is_bounded_and_positive():
    assert elo_adjust_factor(D(0)) == D("1.000")
    assert elo_adjust_factor(D(400)) == D("1.250")
    assert elo_adjust_factor(D(-400)) == D("0.750")

    assert elo_adjust_factor(D(10_000)) <= D("1.250")
    assert elo_adjust_factor(D(-10_000)) >= D("0.750")
    assert elo_adjust_factor(D(-10_000)) > D(0)


def test_elo_adjustment_keeps_lambdas_positive():
    lam_home = D("0.000")
    lam_away = D("0.000")
    f = elo_adjust_factor(D(-100_000))
    lam_home_adj = max(LAMBDA_EPS, q_money(lam_home * f))
    lam_away_adj = max(LAMBDA_EPS, q_money(lam_away / f))
    assert lam_home_adj > 0
    assert lam_away_adj > 0
    assert lam_home_adj == LAMBDA_EPS
    assert lam_away_adj == LAMBDA_EPS

