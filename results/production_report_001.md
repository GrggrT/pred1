# Production Report #001

**Дата**: 2026-03-01
**Период**: 2025-12-12 — 2026-03-01
**Total settled**: 304

> Примечание: для SQL-анализа использованы эквивалентные поля текущей схемы (`WIN/LOSS`, `selection_code`, `initial_odd`), так как в БД нет `WON/LOST`, `selection`, `odd` для таблицы `predictions`.

## 1. Overall Performance

| total_settled | total_won | win_pct | total_profit | roi_pct |
|---:|---:|---:|---:|---:|
| 304 | 136 | 44.7 | -27.05 | -8.9 |

## 2. Per-Market Breakdown

| market | settled | won | lost | win_pct | profit | roi_pct | avg_odd |
|---|---:|---:|---:|---:|---:|---:|---:|
| TOTAL | 146 | 67 | 79 | 45.9 | -6.05 | -4.1 | 2.11 |
| 1X2 | 61 | 31 | 30 | 50.8 | 6.05 | 9.9 | 2.45 |
| BTTS | 43 | 19 | 24 | 44.2 | -7.05 | -16.4 | 1.94 |
| TOTAL_3_5 | 23 | 10 | 13 | 43.5 | -7.36 | -32.0 | 2.12 |
| TOTAL_1_5 | 22 | 3 | 19 | 13.6 | -11.80 | -53.6 | 3.43 |
| DOUBLE_CHANCE | 9 | 6 | 3 | 66.7 | -0.84 | -9.3 | 1.39 |

## 3. Per-League Breakdown

| league_name | settled | won | win_pct | roi_pct |
|---|---:|---:|---:|---:|
| Premier League | 89 | 41 | 46.1 | -6.7 |
| Serie A | 52 | 23 | 44.2 | -12.5 |
| Bundesliga | 51 | 26 | 51.0 | 11.2 |
| La Liga | 42 | 15 | 35.7 | -24.3 |
| Primeira Liga | 35 | 11 | 31.4 | -44.1 |
| Ligue 1 | 35 | 20 | 57.1 | 15.1 |

## 4. Prob Source Analysis

| prob_source | settled | win_pct | roi_pct |
|---|---:|---:|---:|
| unknown | 243 | 43.2 | -13.6 |
| logistic | 34 | 32.4 | -8.2 |
| stacking | 27 | 74.1 | 32.8 |

## 5. Odds Bucket Analysis

| odds_bucket | settled | won | win_pct | roi_pct |
|---|---:|---:|---:|---:|
| 1.00-1.50 | 24 | 16 | 66.7 | -8.2 |
| 1.50-2.00 | 109 | 61 | 56.0 | -2.6 |
| 2.00-2.50 | 73 | 32 | 43.8 | -5.0 |
| 2.50-3.00 | 47 | 14 | 29.8 | -19.2 |
| 3.00+ | 51 | 13 | 25.5 | -18.6 |

## 6. Calibration (1X2)

| prob_bin | n | avg_predicted | actual_win_pct | calibration_error |
|---|---:|---:|---:|---:|
| 0.3-0.4 | 4 | 0.380 | 25.0 | 13.0 |
| 0.4-0.5 | 2 | 0.465 | 50.0 | -3.5 |
| 0.5-0.6 | 1 | 0.533 | 100.0 | -46.7 |
| 0.6-0.7 | 12 | 0.631 | 83.3 | -20.2 |
| 0.7+ | 9 | 0.770 | 77.8 | -0.7 |

## 7. CLV

- n_with_closing: 45
- avg_clv_pct: 0.00

Дополнительно из `production_monitor.py` (stacking subset): Mean CLV `+0.0000` на 27 предсказаниях с closing odds.

## 8. Temporal Trend

| week | settled | roi_pct | profit |
|---|---:|---:|---:|
| 2025-12-08 | 15 | 49.7 | 7.45 |
| 2025-12-15 | 16 | 0.4 | 0.06 |
| 2025-12-22 | 10 | -19.5 | -1.95 |
| 2025-12-29 | 8 | -55.9 | -4.47 |
| 2026-01-05 | 85 | 4.0 | 3.38 |
| 2026-02-16 | 52 | -45.6 | -23.72 |
| 2026-02-23 | 118 | -6.6 | -7.80 |

## 9. Recommendations

- Calibration error (monitor): `0.0732` > `0.05` → **DO NOT activate Kelly**.
- Quarter-Kelly допускается только при calibration error `< 0.05`; текущий уровень выше порога.
- Flat ROI по всем рынкам `-8.9%` (304 settled) → в текущем виде стратегия убыточна в aggregate.
- CLV нейтральный (`0.00%`) → явного edge against market не видно, нужна донастройка value-фильтра и тайминга входа.
- Лучший рынок: `1X2` (`+9.9% ROI`), худшие: `TOTAL_1_5` (`-53.6%`), `TOTAL_3_5` (`-32.0%`), `BTTS` (`-16.4%`), `DOUBLE_CHANCE` при высоком winrate всё равно минусовый (`-9.3%`).
- По лигам: сильные `Ligue 1` (`+15.1%`), `Bundesliga` (`+11.2%`); слабые `Primeira Liga` (`-44.1%`), `La Liga` (`-24.3%`).
- По odds buckets просадка усиливается после `2.50+` (ROI ниже `-18%`) → сузить верхнюю границу odds/поднять EV threshold для longshots.
- Следующие действия:
  1. Временно ужесточить/отключить `TOTAL_1_5`, `TOTAL_3_5`, `BTTS`, `DOUBLE_CHANCE` до стабилизации метрик.
  2. Оставить `1X2` как основной production market, провести отдельный мониторинг по нему (n>=100).
  3. Перекалибровать вероятности на production срезе (Dirichlet/изотоника) и повторно измерить calibration error.
  4. Пересмотреть `VALUE_THRESHOLD` и `ODDS_RANGE` для high-odds сегмента.
  5. Повторить отчёт при достижении 450+ settled и отдельно проверить CLV по bookmaker/времени до kickoff.

## Appendix A: production_monitor.py output

```text
============================================================
  PRODUCTION STACKING MONITOR  (27 settled predictions)
============================================================

--- A. Overall Metrics ---
  RPS (stacking):    0.1493  (backtest: 0.196)
  RPS (DC-only):     0.1617
  RPS (Poisson):     0.1596
  Brier:             0.1827
  LogLoss:           0.5515

--- B. Calibration (mean error: 0.0732) ---
         Bin      N  Expected    Actual     Error
      30-40%      3    0.3764    0.3333    0.0431
      40-50%      2    0.4654    0.5000    0.0346
      60-70%     12    0.6315    0.8333    0.2019
      70-80%      8    0.7633    0.7500    0.0133

--- C. Per-League ---
    League      N       RPS   WinRate       ROI
        39      6    0.1486    83.3%   +44.7%
        61      4    0.1186    75.0%   +46.2%
        78      5    0.2510    60.0%    +9.8%
        94      2    0.0920    50.0%   -21.5%
       135      5    0.1045    80.0%   +23.6%
       140      5    0.1406    80.0%   +61.6%

--- D. Source Breakdown ---
  stacking: 27

--- E. CLV Analysis ---
  Mean CLV: +0.0000 (27 predictions with closing odds)

--- F. Base Model Comparison ---
  Stacking vs DC-only:  -7.7%
  Stacking vs Poisson:  -6.5%

--- G. Financial ---
  Win rate:       74.1%
  ROI (flat):     +32.8%
  Total profit:   +8.85 units
  Kelly eligible: 27/27

============================================================
  RECOMMENDATIONS
============================================================

  [!!] Calibration error 0.0732 > 0.05. DO NOT activate Kelly. Investigate.
  [OK] Production RPS 0.1493 in line with backtest (0.196). Model working.
  [~~] Negative CLV (+0.0000). Model may not have real edge.
  [OK] Stacking outperforms DC-only in production.
```
