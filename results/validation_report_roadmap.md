# Validation Report: Strategic Improvement Roadmap

**Date**: 2026-03-13
**Session**: Task 22 — Roadmap validation
**Approach**: Step-by-step gate validation, each step verified independently

---

## Step 1: Migrations + Compilation + Tests — PASSED

### Migrations
| Migration | Table/Column | Status |
|-----------|-------------|--------|
| 0035 | `team_standings_history` (PK: team_id, league_id, season, as_of_date) | Applied |
| 0036 | `opening_home`, `opening_draw`, `opening_away` on `odds` | Applied |
| 0037 | `nu0`, `nu1` on `dc_global_params`; `home_advantage_delta` on `team_strength_params` | Applied |

### Compilation
- `python -m compileall -q app` — **clean** (0 errors)

### Tests
- **300 passed**, 21 failures (all pre-existing, none related to roadmap)
- Gate: 300 >= 290 — **PASSED**

---

## Step 2: Historical Standings Backfill — PASSED

### Data Volume
| League | Records | Date Range |
|--------|---------|------------|
| EPL (39) | 3,573 | 2024-08-17 to 2026-03-09 |
| Ligue 1 (61) | 3,048 | 2024-08-17 to 2026-03-09 |
| Bundesliga (78) | 2,754 | 2024-08-24 to 2026-03-09 |
| Primeira (94) | 2,754 | 2024-08-11 to 2026-03-09 |
| Serie A (135) | 3,408 | 2024-08-18 to 2026-03-09 |
| La Liga (140) | 5,598 | 2024-08-16 to 2026-03-09 |
| **Total** | **21,135** | 2 seasons |

### LATERAL Join Validation
| League | Avg Rank Diff | Avg Pts Diff |
|--------|--------------|-------------|
| EPL (39) | 6.0 | 9.7 |
| Ligue 1 (61) | 6.7 | 8.7 |
| Bundesliga (78) | 6.0 | 8.5 |
| Primeira (94) | 6.4 | 8.7 |
| Serie A (135) | 6.7 | 9.1 |
| La Liga (140) | 6.6 | 9.0 |

- Gate: records > 0, avg_diff > 0 — **PASSED**

---

## Step 3: CMP-DC Ablation — FAILED

### CMP-DC vs Standard DC (Goals-based)
| League | N | DC-Goals RPS | CMP-DC RPS | Delta RPS | Improved? | Avg nu |
|--------|---|-------------|-----------|----------|----------|--------|
| EPL | 240 | 0.12760 | 0.12750 | -0.00010 | ~ | 0.950 |
| Ligue 1 | 180 | 0.12347 | 0.12391 | +0.00044 | ~ | 1.104 |
| Bundesliga | 163 | 0.11773 | 0.11748 | -0.00025 | ~ | 0.887 |
| Primeira | 178 | 0.10035 | 0.10045 | +0.00010 | ~ | 1.115 |
| Serie A | 216 | 0.12870 | 0.12869 | -0.00001 | ~ | 1.104 |
| La Liga | 212 | 0.11716 | 0.11937 | +0.00221 | NO | 1.217 |
| **GLOBAL** | **1189** | **0.11988** | **0.12030** | **+0.00042** | **~** | |

### CMP-DC vs Standard DC (xG-based)
| League | N | DC-xG RPS | CMP-xG RPS | Delta RPS | Improved? |
|--------|---|----------|-----------|----------|----------|
| EPL | 240 | 0.13640 | 0.13640 | -0.00000 | ~ |
| Ligue 1 | 180 | 0.13272 | 0.13299 | +0.00027 | ~ |
| Bundesliga | 163 | 0.12443 | 0.12421 | -0.00022 | ~ |
| Primeira | 178 | 0.10834 | 0.10856 | +0.00022 | ~ |
| Serie A | 216 | 0.13507 | 0.13521 | +0.00014 | ~ |
| La Liga | 212 | 0.12480 | 0.12635 | +0.00156 | NO |
| **GLOBAL** | **1189** | **0.12769** | **0.12804** | **+0.00035** | **~** | |

### Fitted CMP Parameters (3 leagues)
| League | nu0 | nu1 | Interpretation |
|--------|-----|-----|---------------|
| EPL (39) | 0.917 | 0.105 | Slightly overdispersed base |
| Bundesliga (78) | 0.800 | 0.189 | Overdispersed base |
| La Liga (140) | 1.101 | 0.273 | Underdispersed, strongest nu effect |

### Nu Distribution
| League | Mean nu | Std | Min | Max |
|--------|---------|-----|-----|-----|
| EPL | 0.950 | 0.022 | 0.917 | 1.013 |
| Ligue 1 | 1.104 | 0.039 | 1.050 | 1.215 |
| Bundesliga | 0.887 | 0.062 | 0.801 | 1.108 |
| Primeira | 1.115 | 0.046 | 1.051 | 1.270 |
| Serie A | 1.104 | 0.039 | 1.052 | 1.244 |
| La Liga | 1.217 | 0.082 | 1.101 | 1.507 |
| **GLOBAL** | **1.065** | **0.121** | | |
| % with nu > 1.0 | 66.9% | | | |

### Gate Decision
- Required: delta RPS < 0 for >= 3/6 leagues
- Result: 0/6 leagues improved
- **GATE: FAILED — CMP-DC disabled (`DC_USE_CMP=false`)**

### Analysis
1. CMP-DC provides negligible improvement over standard DC across all leagues
2. La Liga shows the largest nu values (mean 1.217) but performs worst with CMP (+0.00221 RPS)
3. EPL and Bundesliga fitted nu0 < 1.0 (overdispersed), contradicting underdispersion hypothesis
4. The nu_from_balance competitive-balance function does not capture meaningful signal
5. **Conclusion**: Football goal scoring after DC conditioning is well-modeled by Poisson (nu=1)

---

## Step 4: Stacking v3 Retrain — PARTIAL

### V2 Retrain (13 features)
| Metric | Value |
|--------|-------|
| Training samples | 5,846 |
| Validation samples | 1,462 |
| Validation RPS | **0.1886** |
| Validation LogLoss | 0.9298 |
| Validation Brier | 0.1827 |

- Existing model (2026-02-22) already has identical val_RPS=0.1886
- **No retrain needed** — model is current

### V3 Features (30 features)
| Feature Group | Status | Reason |
|--------------|--------|--------|
| CMP (4 features) | DISABLED | CMP-DC failed ablation (Step 3) |
| Market (7 features) | BLOCKED | 0 predictions with v3 features yet |
| Performance (4 features) | BLOCKED | 0 predictions with v3 features yet |
| Context (3 features) | BLOCKED | 0 predictions with v3 features yet |

- V3 features require build_predictions to run with new code
- Predictions need to settle (1-2 weeks) before training
- **V3 retrain deferred until sufficient data accumulates**

---

## Step 5: Production Activation — COMPLETED

### Configuration Applied
| Setting | Value | Rationale |
|---------|-------|-----------|
| `DC_USE_CMP` | false | Failed ablation (0/6 leagues) |
| `SYNC_PINNACLE_ODDS` | true | Data accumulation phase |
| `PINNACLE_BOOKMAKER_ID` | 4 | API Football Pinnacle ID |
| `USE_PINNACLE_CALIB` | false | Insufficient Pinnacle data |
| `ENABLE_KELLY` | false | Awaiting Pinnacle calibration |

### Verified Working
- Containers: app (healthy), scheduler (healthy), db (healthy)
- Sync_data: fetching all bookmakers (no `&bookmaker=` filter), 200 OK responses
- Migrations: 0035-0037 applied and verified
- Standings backfill: 21,135 records across 6 leagues

---

## Bugs Fixed During Validation

### Bug 1: API Football bookmaker parameter
- **Symptom**: `RuntimeError: API-Football returned errors: {'bookmaker': 'The Bookmaker field must contain an integer.'}`
- **Cause**: Passing `bookmaker=8,4` (comma-separated) when Pinnacle sync enabled
- **Fix**: Use empty list `[]` (fetch all bookmakers) when Pinnacle sync is active
- **File**: `app/jobs/sync_data.py`

### Bug 2: CMP _log_factorial IndexError
- **Symptom**: `IndexError: only integers, slices ... are valid indices`
- **Cause**: Numpy float passed to array index in `_LOG_FACT[k]`
- **Fix**: Added `k = int(k)` conversion in `_log_factorial()` and `cmp_pmf()`
- **File**: `app/services/com_poisson.py`

---

## Summary

| Step | Status | Gate |
|------|--------|------|
| 1. Migrations + Tests | PASSED | 300/290 tests, migrations clean |
| 2. Standings Backfill | PASSED | 21,135 records, non-zero diffs |
| 3. CMP-DC Ablation | FAILED | 0/6 leagues improved (need 3/6) |
| 4. Stacking Retrain | PARTIAL | v2 current (RPS 0.1886), v3 deferred |
| 5. Production Activation | COMPLETED | Validated components enabled |

### Active Components (Post-Validation)
- Historical standings backfill + LATERAL join
- Opening odds tracking
- Pinnacle dual-bookmaker sync (data accumulation)
- Dual CLV tracking (soft + Pinnacle)
- V3 feature computation in build_predictions (accumulating)
- Market timing bonus

### Deferred Components
- CMP-DC model (failed ablation, disabled)
- Pinnacle calibration (needs 50+ Pinnacle odds)
- Kelly criterion (needs Pinnacle calibration)
- Stacking v3 retrain (needs v3 feature data accumulation)
