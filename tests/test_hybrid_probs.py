from decimal import Decimal

from app.core.config import settings
from app.jobs.build_predictions import logistic_probs_from_features
from app.services.poisson import match_probs, match_probs_dixon_coles


def test_hybrid_probs_sum_to_one():
    lam_h = Decimal("1.2")
    lam_a = Decimal("1.1")
    ph, pd, pa = match_probs(lam_h, lam_a, k_max=10)
    dh, dd, da = match_probs_dixon_coles(lam_h, lam_a, rho=Decimal("0.1"), k_max=10)
    lh, ld, la = logistic_probs_from_features(Decimal("50"), Decimal("0.2"), Decimal("0.22"))
    weights = {"poisson": Decimal("0.3"), "dixon_coles": Decimal("0.2"), "logistic": Decimal("0.5")}
    final = {
        "home": weights["poisson"] * ph + weights["dixon_coles"] * dh + weights["logistic"] * lh,
        "draw": weights["poisson"] * pd + weights["dixon_coles"] * dd + weights["logistic"] * ld,
        "away": weights["poisson"] * pa + weights["dixon_coles"] * da + weights["logistic"] * la,
    }
    total = final["home"] + final["draw"] + final["away"]
    assert abs(total - Decimal("1")) < Decimal("0.01")
