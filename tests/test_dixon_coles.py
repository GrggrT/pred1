"""Tests for Dixon-Coles model."""

import math
from datetime import date, timedelta

import pytest

from app.services.dixon_coles import (
    DCParams,
    MatchData,
    fit_dixon_coles,
    predict_lambda_mu,
    tau_value,
)


def _make_round_robin(
    team_ids: list[int],
    scores: dict[tuple[int, int], tuple[int, int]],
    start_date: date,
) -> list[MatchData]:
    """Generate round-robin matches with specified scores."""
    matches = []
    day = 0
    for home in team_ids:
        for away in team_ids:
            if home == away:
                continue
            hg, ag = scores.get((home, away), (1, 1))
            matches.append(
                MatchData(
                    home_id=home,
                    away_id=away,
                    home_goals=hg,
                    away_goals=ag,
                    date=start_date + timedelta(days=day),
                )
            )
            day += 1
    return matches


def _make_synthetic_league(n_teams: int = 6, n_rounds: int = 3) -> list[MatchData]:
    """Generate a synthetic league with home advantage and varied team strength."""
    teams = list(range(1, n_teams + 1))
    matches = []
    start = date(2024, 8, 1)
    day = 0
    for _ in range(n_rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                # Stronger teams (lower id) score more
                home_strength = n_teams + 1 - home
                away_strength = n_teams + 1 - away
                # Home advantage: +1 goal
                hg = max(0, home_strength // 2 + 1)
                ag = max(0, away_strength // 2)
                matches.append(
                    MatchData(
                        home_id=home,
                        away_id=away,
                        home_goals=hg,
                        away_goals=ag,
                        date=start + timedelta(days=day),
                    )
                )
                day += 1
    return matches


class TestTauValue:
    def test_rho_zero_returns_one(self):
        """tau with rho=0 is pure Poisson (returns 1.0 for all scores)."""
        for x in range(5):
            for y in range(5):
                assert tau_value(x, y, 1.5, 1.2, 0.0) == 1.0

    def test_00_correction(self):
        """tau(0,0) = 1 - lam*mu*rho."""
        lam, mu, rho = 1.5, 1.2, 0.1
        assert tau_value(0, 0, lam, mu, rho) == pytest.approx(1 - 1.5 * 1.2 * 0.1)

    def test_01_correction(self):
        """tau(0,1) = 1 + lam*rho."""
        assert tau_value(0, 1, 1.5, 1.2, 0.1) == pytest.approx(1 + 1.5 * 0.1)

    def test_10_correction(self):
        """tau(1,0) = 1 + mu*rho."""
        assert tau_value(1, 0, 1.5, 1.2, 0.1) == pytest.approx(1 + 1.2 * 0.1)

    def test_11_correction(self):
        """tau(1,1) = 1 - rho."""
        assert tau_value(1, 1, 1.5, 1.2, 0.1) == pytest.approx(1 - 0.1)

    def test_high_scores_return_one(self):
        """tau for goals >= 2 always returns 1.0."""
        assert tau_value(2, 3, 1.5, 1.2, 0.1) == 1.0
        assert tau_value(0, 2, 1.5, 1.2, 0.1) == 1.0
        assert tau_value(3, 0, 1.5, 1.2, 0.1) == 1.0


class TestPredictLambdaMu:
    def test_basic_computation(self):
        """lam = exp(HA + att_h + def_a), mu = exp(att_a + def_h)."""
        att_h, def_h = 0.2, -0.1
        att_a, def_a = -0.1, 0.3
        ha = 0.25

        lam, mu = predict_lambda_mu(att_h, def_h, att_a, def_a, ha)
        assert lam == pytest.approx(math.exp(ha + att_h + def_a))
        assert mu == pytest.approx(math.exp(att_a + def_h))

    def test_positive_output(self):
        """Lambda and mu are always > 0."""
        lam, mu = predict_lambda_mu(-2.0, 2.0, -2.0, 2.0, -1.0)
        assert lam > 0
        assert mu > 0

    def test_symmetric_no_ha(self):
        """Without HA, symmetric teams give lam == mu when att/def mirror."""
        lam, mu = predict_lambda_mu(0.0, 0.0, 0.0, 0.0, 0.0)
        assert lam == pytest.approx(mu)
        assert lam == pytest.approx(1.0)


class TestFitDixonColes:
    @pytest.fixture
    def synthetic_matches(self) -> list[MatchData]:
        return _make_synthetic_league(n_teams=6, n_rounds=2)

    @pytest.fixture
    def ref_date(self) -> date:
        return date(2025, 6, 1)

    def test_sum_to_zero_constraint(self, synthetic_matches, ref_date):
        """Attack and defense params must sum to approximately zero."""
        params = fit_dixon_coles(synthetic_matches, ref_date, xi=0.0, rho_grid_steps=5)

        att_sum = sum(params.attack.values())
        def_sum = sum(params.defense.values())
        assert abs(att_sum) < 1e-6, f"Attack sum={att_sum}"
        assert abs(def_sum) < 1e-6, f"Defense sum={def_sum}"

    def test_lambda_mu_positive(self, synthetic_matches, ref_date):
        """All predicted lambdas and mus must be positive."""
        params = fit_dixon_coles(synthetic_matches, ref_date, xi=0.0, rho_grid_steps=5)

        team_ids = list(params.attack.keys())
        for h in team_ids:
            for a in team_ids:
                if h == a:
                    continue
                lam, mu = predict_lambda_mu(
                    params.attack[h], params.defense[h],
                    params.attack[a], params.defense[a],
                    params.home_advantage,
                )
                assert lam > 0, f"lam <= 0 for {h} vs {a}"
                assert mu > 0, f"mu <= 0 for {h} vs {a}"

    def test_strong_vs_weak_team(self, ref_date):
        """Strong team (wins big) should have higher attack than weak team."""
        teams = [1, 2, 3, 4, 5]
        scores = {}
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                if h == 1:  # Team 1 is dominant
                    scores[(h, a)] = (4, 0)
                elif a == 1:  # Everyone loses to team 1
                    scores[(h, a)] = (0, 4)
                elif h == 5:  # Team 5 is weakest
                    scores[(h, a)] = (0, 3)
                elif a == 5:
                    scores[(h, a)] = (3, 0)
                else:
                    scores[(h, a)] = (1, 1)

        matches = _make_round_robin(teams, scores, date(2024, 8, 1))
        # Two rounds for more data
        matches2 = _make_round_robin(teams, scores, date(2024, 10, 1))
        all_matches = matches + matches2

        params = fit_dixon_coles(all_matches, ref_date, xi=0.0, rho_grid_steps=5)

        assert params.attack[1] > params.attack[5], (
            f"Strong team att={params.attack[1]:.4f} should > weak team att={params.attack[5]:.4f}"
        )
        # Weak team's defense should be higher (concedes more)
        assert params.defense[5] > params.defense[1], (
            f"Weak team def={params.defense[5]:.4f} should > strong team def={params.defense[1]:.4f}"
        )

    def test_home_advantage_positive(self, ref_date):
        """With home-dominant data, HA should be positive."""
        teams = [1, 2, 3, 4, 5, 6]
        scores = {}
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                # Home always scores more
                scores[(h, a)] = (3, 1)

        matches = _make_round_robin(teams, scores, date(2024, 8, 1))
        matches2 = _make_round_robin(teams, scores, date(2024, 10, 1))
        all_matches = matches + matches2

        params = fit_dixon_coles(all_matches, ref_date, xi=0.0, rho_grid_steps=5)

        assert params.home_advantage > 0, (
            f"HA={params.home_advantage:.4f} should be positive with home-dominant data"
        )

    def test_time_decay_changes_result(self, ref_date):
        """Different xi values should produce different parameters."""
        teams = [1, 2, 3, 4, 5]
        # Early: team 1 dominant
        early_scores = {}
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                if h == 1:
                    early_scores[(h, a)] = (4, 0)
                elif a == 1:
                    early_scores[(h, a)] = (0, 4)
                else:
                    early_scores[(h, a)] = (1, 1)

        # Late: team 5 dominant (team 1 collapsed)
        late_scores = {}
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                if h == 5:
                    late_scores[(h, a)] = (4, 0)
                elif a == 5:
                    late_scores[(h, a)] = (0, 4)
                else:
                    late_scores[(h, a)] = (1, 1)

        early = _make_round_robin(teams, early_scores, date(2024, 1, 1))
        late = _make_round_robin(teams, late_scores, date(2024, 10, 1))
        all_matches = early + late

        params_no_decay = fit_dixon_coles(all_matches, ref_date, xi=0.0, rho_grid_steps=5)
        params_high_decay = fit_dixon_coles(all_matches, ref_date, xi=0.02, rho_grid_steps=5)

        # With high decay, recent matches (where team 5 is dominant) should be
        # weighted more, so team 5's attack should be higher relative to no-decay
        assert params_high_decay.attack[5] > params_no_decay.attack[5], (
            "High decay should favor recent form (team 5 dominant lately)"
        )

    def test_too_few_matches_raises(self, ref_date):
        """Should raise ValueError with < 10 matches."""
        matches = [
            MatchData(1, 2, 1, 0, date(2024, 8, 1)),
            MatchData(2, 1, 0, 1, date(2024, 8, 8)),
        ]
        with pytest.raises(ValueError, match="Too few"):
            fit_dixon_coles(matches, ref_date)

    def test_n_matches_and_n_teams(self, synthetic_matches, ref_date):
        """DCParams should report correct counts."""
        params = fit_dixon_coles(synthetic_matches, ref_date, xi=0.0, rho_grid_steps=5)
        assert params.n_matches == len(synthetic_matches)
        assert params.n_teams == 6
        assert len(params.attack) == 6
        assert len(params.defense) == 6


# ---------------------------------------------------------------------------
# xG mode tests
# ---------------------------------------------------------------------------

def _make_synthetic_league_xg(n_teams: int = 6, n_rounds: int = 3) -> list[MatchData]:
    """Generate a synthetic league with xG = goals + gaussian noise."""
    import random
    rng = random.Random(42)
    teams = list(range(1, n_teams + 1))
    matches = []
    start = date(2024, 8, 1)
    day = 0
    for _ in range(n_rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                home_strength = n_teams + 1 - home
                away_strength = n_teams + 1 - away
                hg = max(0, home_strength // 2 + 1)
                ag = max(0, away_strength // 2)
                # xG = goals + noise (clamp >= 0)
                h_xg = max(0.0, hg + rng.gauss(0, 0.3))
                a_xg = max(0.0, ag + rng.gauss(0, 0.3))
                matches.append(
                    MatchData(
                        home_id=home,
                        away_id=away,
                        home_goals=hg,
                        away_goals=ag,
                        date=start + timedelta(days=day),
                        home_xg=round(h_xg, 2),
                        away_xg=round(a_xg, 2),
                    )
                )
                day += 1
    return matches


class TestFitDixonColesXg:
    @pytest.fixture
    def xg_matches(self) -> list[MatchData]:
        return _make_synthetic_league_xg(n_teams=6, n_rounds=2)

    @pytest.fixture
    def ref_date(self) -> date:
        return date(2025, 6, 1)

    def test_xg_fit_converges(self, xg_matches, ref_date):
        """xG fit converges, rho==0, sum-to-zero constraint holds."""
        params = fit_dixon_coles(xg_matches, ref_date, xi=0.0, use_xg=True)

        assert params.rho == 0.0
        assert params.n_teams == 6
        assert params.n_matches > 0

        att_sum = sum(params.attack.values())
        def_sum = sum(params.defense.values())
        assert abs(att_sum) < 1e-6, f"Attack sum={att_sum}"
        assert abs(def_sum) < 1e-6, f"Defense sum={def_sum}"

    def test_xg_skips_missing(self, ref_date):
        """Matches without xG data are skipped in xG mode."""
        teams = list(range(1, 7))
        matches = []
        start = date(2024, 8, 1)
        day = 0
        for _ in range(3):
            for h in teams:
                for a in teams:
                    if h == a:
                        continue
                    # Half matches have xG, half don't
                    has_xg = (day % 2 == 0)
                    matches.append(
                        MatchData(
                            home_id=h, away_id=a,
                            home_goals=1, away_goals=1,
                            date=start + timedelta(days=day),
                            home_xg=1.1 if has_xg else None,
                            away_xg=0.9 if has_xg else None,
                        )
                    )
                    day += 1

        params = fit_dixon_coles(matches, ref_date, xi=0.0, use_xg=True)
        # Only ~half the matches should be used
        total_possible = len([m for m in matches if m.home_xg is not None])
        assert params.n_matches == total_possible

    def test_xg_vs_goals_different(self, xg_matches, ref_date):
        """xG and goals modes produce different parameters on the same data."""
        params_goals = fit_dixon_coles(xg_matches, ref_date, xi=0.0, rho_grid_steps=5, use_xg=False)
        params_xg = fit_dixon_coles(xg_matches, ref_date, xi=0.0, use_xg=True)

        # Parameters should differ (xG has noise relative to goals)
        diffs = [
            abs(params_goals.attack[tid] - params_xg.attack[tid])
            for tid in params_goals.attack
        ]
        assert max(diffs) > 1e-4, "xG and goals params should differ"

    def test_use_xg_false_unchanged(self, ref_date):
        """use_xg=False produces identical results to default (regression test)."""
        matches = _make_synthetic_league(n_teams=6, n_rounds=2)
        params_default = fit_dixon_coles(matches, ref_date, xi=0.0, rho_grid_steps=5)
        params_explicit = fit_dixon_coles(matches, ref_date, xi=0.0, rho_grid_steps=5, use_xg=False)

        assert params_default.rho == pytest.approx(params_explicit.rho)
        assert params_default.home_advantage == pytest.approx(params_explicit.home_advantage)
        for tid in params_default.attack:
            assert params_default.attack[tid] == pytest.approx(params_explicit.attack[tid])
