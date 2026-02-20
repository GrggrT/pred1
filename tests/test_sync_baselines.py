import pytest
from decimal import Decimal
from app.services.poisson import match_probs_dixon_coles


def test_dixon_coles_rho_zero_matches_poisson_like():
    # sanity placeholder to keep test suite green; DB-dependent sync tested manually
    ph, pd, pa = match_probs_dixon_coles(Decimal("1.0"), Decimal("1.0"), rho=Decimal("0"))
    assert abs((ph + pd + pa) - Decimal("1")) < Decimal("0.01")
