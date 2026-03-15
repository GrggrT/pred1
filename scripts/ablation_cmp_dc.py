"""
CMP-DC Ablation Study: Compare standard DC vs CMP-DC on historical data.

Uses psycopg2 (sync) for direct DB access, like generate_training_data.py.

Usage:
    python scripts/ablation_cmp_dc.py
"""
from __future__ import annotations

import math
import os
import sys
from collections import defaultdict
from decimal import Decimal

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.services.poisson import match_probs_dixon_coles
from app.services.com_poisson import match_probs_cmp_dc, nu_from_balance


D = Decimal


def _rps(probs: list[float], outcome: list[int]) -> float:
    """Ranked Probability Score for 1X2."""
    cum_p = np.cumsum(probs)
    cum_o = np.cumsum(outcome)
    return float(np.mean((cum_p - cum_o) ** 2))


def _get_conn():
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise ValueError("DATABASE_URL not set")
    import psycopg2
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(dsn)


def main():
    conn = _get_conn()
    cur = conn.cursor()

    # Load DC global params (goals + xg)
    cur.execute("""
        SELECT league_id, season, param_source, rho, home_advantage, nu0, nu1
        FROM dc_global_params
        WHERE param_source IN ('goals', 'xg')
        ORDER BY league_id, season, param_source
    """)
    dc_globals = {}
    for row in cur.fetchall():
        lid, season, ps, rho, ha, nu0, nu1 = row
        key = (lid, season, ps)
        dc_globals[key] = {
            "rho": float(rho) if rho is not None else 0.0,
            "ha": float(ha) if ha is not None else 0.3,
            "nu0": float(nu0) if nu0 is not None else 1.05,
            "nu1": float(nu1) if nu1 is not None else 0.15,
        }

    # Load fitted CMP params (override nu0/nu1 where available)
    cur.execute("""
        SELECT league_id, season, nu0, nu1
        FROM dc_global_params
        WHERE param_source = 'cmp' AND nu0 IS NOT NULL
    """)
    cmp_fitted = {}
    for row in cur.fetchall():
        lid, season, nu0_fit, nu1_fit = row
        cmp_fitted[(lid, season)] = (float(nu0_fit), float(nu1_fit))
        # Override nu0/nu1 in goals params if CMP fitted available
        gkey = (lid, season, "goals")
        if gkey in dc_globals:
            dc_globals[gkey]["nu0"] = float(nu0_fit)
            dc_globals[gkey]["nu1"] = float(nu1_fit)
        xkey = (lid, season, "xg")
        if xkey in dc_globals:
            dc_globals[xkey]["nu0"] = float(nu0_fit)
            dc_globals[xkey]["nu1"] = float(nu1_fit)
    print(f"CMP fitted params available for {len(cmp_fitted)} league-seasons: "
          f"{list(cmp_fitted.keys())}")

    # Load team strengths
    cur.execute("""
        SELECT team_id, league_id, season, param_source,
               attack, defense, home_advantage_delta
        FROM team_strength_params
        WHERE param_source IN ('goals', 'xg')
    """)
    team_strengths = {}
    for row in cur.fetchall():
        tid, lid, season, ps, att, df, ha_d = row
        key = (tid, lid, season, ps)
        team_strengths[key] = {
            "attack": float(att),
            "defense": float(df),
            "ha_delta": float(ha_d) if ha_d else 0.0,
        }

    # Load finished fixtures
    cur.execute("""
        SELECT id, league_id, season, home_team_id, away_team_id,
               home_goals, away_goals
        FROM fixtures
        WHERE status = 'FT'
          AND home_goals IS NOT NULL
          AND away_goals IS NOT NULL
        ORDER BY kickoff ASC
    """)
    fixtures = cur.fetchall()
    cur.close()
    conn.close()

    print(f"Loaded {len(fixtures)} finished fixtures")
    print(f"DC global params: {len(dc_globals)} entries")
    print(f"Team strengths: {len(team_strengths)} entries")
    print()

    league_results = defaultdict(lambda: {
        "dc_rps": [], "cmp_rps": [],
        "dc_xg_rps": [], "cmp_xg_rps": [],
        "nu_values": [], "count": 0
    })

    skipped = 0
    for fix in fixtures:
        fid, lid, season, htid, atid, hg, ag = fix

        dc_key = (lid, season, "goals")
        if dc_key not in dc_globals:
            skipped += 1
            continue

        home_key = (htid, lid, season, "goals")
        away_key = (atid, lid, season, "goals")
        if home_key not in team_strengths or away_key not in team_strengths:
            skipped += 1
            continue

        g = dc_globals[dc_key]
        h = team_strengths[home_key]
        a = team_strengths[away_key]

        lam_home = math.exp(h["attack"] + a["defense"] + g["ha"])
        lam_away = math.exp(a["attack"] + h["defense"])
        lam_home = max(0.1, min(lam_home, 5.0))
        lam_away = max(0.1, min(lam_away, 5.0))

        rho = g["rho"]

        # Standard DC (nu=1)
        ph_dc, pd_dc, pa_dc = match_probs_dixon_coles(
            D(str(round(lam_home, 6))), D(str(round(lam_away, 6))),
            rho=D(str(round(rho, 4))), k_max=8
        )
        dc_probs = [float(ph_dc), float(pd_dc), float(pa_dc)]

        # CMP-DC with dispersion
        att_diff = abs(h["attack"] - a["attack"])
        nu = nu_from_balance(att_diff, g["nu0"], g["nu1"])
        ha_team = g["ha"] + h.get("ha_delta", 0.0)
        lam_home_cmp = math.exp(h["attack"] + a["defense"] + ha_team)
        lam_home_cmp = max(0.1, min(lam_home_cmp, 5.0))

        ph_cmp, pd_cmp, pa_cmp = match_probs_cmp_dc(
            D(str(round(lam_home_cmp, 6))), D(str(round(lam_away, 6))),
            nu=nu, rho=D(str(round(rho, 4))), k_max=8
        )
        cmp_probs = [float(ph_cmp), float(pd_cmp), float(pa_cmp)]

        # Outcome
        if hg > ag:
            outcome = [1, 0, 0]
        elif hg == ag:
            outcome = [0, 1, 0]
        else:
            outcome = [0, 0, 1]

        dc_rps_val = _rps(dc_probs, outcome)
        cmp_rps_val = _rps(cmp_probs, outcome)

        r = league_results[lid]
        r["dc_rps"].append(dc_rps_val)
        r["cmp_rps"].append(cmp_rps_val)
        r["nu_values"].append(nu)
        r["count"] += 1

        # DC-xG
        xg_key = (lid, season, "xg")
        hx_key = (htid, lid, season, "xg")
        ax_key = (atid, lid, season, "xg")
        if xg_key in dc_globals and hx_key in team_strengths and ax_key in team_strengths:
            gx = dc_globals[xg_key]
            hx = team_strengths[hx_key]
            ax = team_strengths[ax_key]
            lh_xg = min(max(math.exp(hx["attack"] + ax["defense"] + gx["ha"]), 0.1), 5.0)
            la_xg = min(max(math.exp(ax["attack"] + hx["defense"]), 0.1), 5.0)
            ph_xg, pd_xg, pa_xg = match_probs_dixon_coles(
                D(str(round(lh_xg, 6))), D(str(round(la_xg, 6))),
                rho=D(str(round(gx["rho"], 4))), k_max=8
            )
            r["dc_xg_rps"].append(_rps([float(ph_xg), float(pd_xg), float(pa_xg)], outcome))

            att_d_xg = abs(hx["attack"] - ax["attack"])
            nu_xg = nu_from_balance(att_d_xg, gx["nu0"], gx["nu1"])
            ha_xg = gx["ha"] + hx.get("ha_delta", 0.0)
            lh_xg_c = min(max(math.exp(hx["attack"] + ax["defense"] + ha_xg), 0.1), 5.0)
            ph_xgc, pd_xgc, pa_xgc = match_probs_cmp_dc(
                D(str(round(lh_xg_c, 6))), D(str(round(la_xg, 6))),
                nu=nu_xg, rho=D(str(round(gx["rho"], 4))), k_max=8
            )
            r["cmp_xg_rps"].append(_rps([float(ph_xgc), float(pd_xgc), float(pa_xgc)], outcome))

    # Results
    league_names = {39: "EPL", 61: "Ligue 1", 78: "Bundesliga",
                    94: "Primeira", 135: "Serie A", 140: "La Liga"}

    print("=" * 90)
    print(f"{'League':<12} | {'N':>5} | {'DC-Goals RPS':>12} | {'CMP-DC RPS':>11} | {'ΔRPS':>8} | {'Imprv?':>6} | {'Avg ν':>6}")
    print("-" * 90)

    all_dc, all_cmp = [], []
    improved_count = 0

    for lid in sorted(league_results.keys()):
        r = league_results[lid]
        name = league_names.get(lid, f"L{lid}")
        n = r["count"]
        if n == 0:
            continue
        dc_avg = np.mean(r["dc_rps"])
        cmp_avg = np.mean(r["cmp_rps"])
        delta = cmp_avg - dc_avg
        improved = "YES" if delta < -0.0005 else ("~" if abs(delta) < 0.0005 else "NO")
        if delta < -0.0005:
            improved_count += 1
        nu_avg = np.mean(r["nu_values"])

        print(f"{name:<12} | {n:>5} | {dc_avg:>12.5f} | {cmp_avg:>11.5f} | {delta:>+8.5f} | {improved:>6} | {nu_avg:>6.3f}")
        all_dc.extend(r["dc_rps"])
        all_cmp.extend(r["cmp_rps"])

    print("-" * 90)
    if all_dc:
        dc_g = np.mean(all_dc)
        cmp_g = np.mean(all_cmp)
        d_g = cmp_g - dc_g
        imp_g = "YES" if d_g < -0.0005 else ("~" if abs(d_g) < 0.0005 else "NO")
        print(f"{'GLOBAL':<12} | {len(all_dc):>5} | {dc_g:>12.5f} | {cmp_g:>11.5f} | {d_g:>+8.5f} | {imp_g:>6} |")

    # DC-xG comparison
    all_dc_xg, all_cmp_xg = [], []
    has_xg = False
    for lid in sorted(league_results.keys()):
        r = league_results[lid]
        if r["dc_xg_rps"]:
            has_xg = True
            all_dc_xg.extend(r["dc_xg_rps"])
            all_cmp_xg.extend(r["cmp_xg_rps"])

    if has_xg:
        print()
        print("=" * 80)
        print(f"{'League':<12} | {'N':>5} | {'DC-xG RPS':>12} | {'CMP-xG RPS':>11} | {'ΔRPS':>8} | {'Imprv?':>6}")
        print("-" * 80)
        for lid in sorted(league_results.keys()):
            r = league_results[lid]
            if not r["dc_xg_rps"]:
                continue
            name = league_names.get(lid, f"L{lid}")
            n_xg = len(r["dc_xg_rps"])
            dc_xg = np.mean(r["dc_xg_rps"])
            cmp_xg = np.mean(r["cmp_xg_rps"])
            d = cmp_xg - dc_xg
            imp = "YES" if d < -0.0005 else ("~" if abs(d) < 0.0005 else "NO")
            print(f"{name:<12} | {n_xg:>5} | {dc_xg:>12.5f} | {cmp_xg:>11.5f} | {d:>+8.5f} | {imp:>6}")
        print("-" * 80)
        if all_dc_xg:
            dc_xg_g = np.mean(all_dc_xg)
            cmp_xg_g = np.mean(all_cmp_xg)
            dg = cmp_xg_g - dc_xg_g
            ig = "YES" if dg < -0.0005 else ("~" if abs(dg) < 0.0005 else "NO")
            print(f"{'GLOBAL':<12} | {len(all_dc_xg):>5} | {dc_xg_g:>12.5f} | {cmp_xg_g:>11.5f} | {dg:>+8.5f} | {ig:>6}")

    # ν analysis
    print()
    print("=" * 60)
    print("ν Distribution Analysis:")
    all_nu = []
    for lid in sorted(league_results.keys()):
        r = league_results[lid]
        if not r["nu_values"]:
            continue
        name = league_names.get(lid, f"L{lid}")
        nus = r["nu_values"]
        all_nu.extend(nus)
        print(f"  {name}: mean={np.mean(nus):.3f} std={np.std(nus):.3f} "
              f"min={np.min(nus):.3f} max={np.max(nus):.3f}")

    all_nu = np.array(all_nu)
    print(f"\n  GLOBAL: mean={np.mean(all_nu):.3f} std={np.std(all_nu):.3f}")
    pct_gt_1 = np.mean(all_nu > 1.0) * 100
    print(f"  % with ν > 1.0: {pct_gt_1:.1f}%")

    print(f"\n  Improved in {improved_count}/{len(league_results)} leagues")
    print(f"  Skipped {skipped} fixtures (missing params)")

    # Gate decision
    total_leagues = len([lid for lid in league_results if league_results[lid]["count"] > 0])
    print(f"\n{'='*60}")
    print(f"GATE DECISION:")
    if improved_count >= 3:
        print(f"  CMP-DC improves {improved_count}/{total_leagues} leagues → PASS (ENABLE)")
    elif improved_count >= 1 and all_dc and (np.mean(all_cmp) < np.mean(all_dc)):
        print(f"  CMP-DC improves globally but only {improved_count}/{total_leagues} per-league → CAUTIOUS ENABLE")
    else:
        print(f"  CMP-DC does NOT improve enough ({improved_count}/{total_leagues}) → FAIL (DO NOT ENABLE)")


if __name__ == "__main__":
    main()
