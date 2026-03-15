# Стратегический план улучшения модели

## Текущее состояние (honest assessment)

**Что работает**: Stacking pipeline (RPS 0.1943 ≈ backtest), бьёт DC-only на 6.2%.
**Что не работает**: CLV = 0.00%, ROI +0.8% ≈ break-even, calibration error 0.14.

**Диагноз**: Модель предсказывает исходы на уровне рынка, но не лучше. Она не знает ничего, чего не знает букмекер. Нынешний pipeline — чистый, стабильный, но недостаточно глубокий.

---

## Три уровня проблемы

### Уровень 1: Базовая модель (Dixon-Coles) — неправильная спецификация

Текущий DC использует стандартный Poisson, который:
- Предполагает dispersion = 1. Исследования (Florez et al., JQAS 2025) показывают: голы **underdispersed** (ν > 1) после кондиционирования на силы команд. При мисматчах (λ=3.0) Poisson предсказывает слишком толстый правый хвост — реальные команды снижают темп при комфортном лидерстве.
- Использует **глобальный** home advantage γ. В реальности HA варьируется от 0.05 до 0.35 между командами.
- Корректирует зависимость (τ) только для счётов (0,0), (0,1), (1,0), (1,1). Michels et al. (JRSS-C, 2025) показали: диапазон достижимых корреляций в стандартном DC **экстремально узок** (±0.03).
- Использует одинаковый time-decay для attack и defense. Stüttgen (2025): fixture congestion снижает attack, но улучшает defense — они имеют разную динамику.

### Уровень 2: Стэкинг — не использует доступную информацию

Текущие 13 features:
- DC probs (3), DC-xG probs (3), Poisson probs (3) — все три коррелированы, все основаны на одном Poisson assumption
- Elo (1), fair odds (1), rest_hours (1), standings_delta (1 — всегда 0!)

Чего нет:
- Odds movement (opening → pre-match). Это **главный** недоиспользованный сигнал — мы собираем snapshots, но не используем
- xG breakdown (chance quality, shot volume — не просто итоговый xG)
- Form trajectory (не средняя форма, а тренд)
- Squad/lineup data (ротация, ключевые отсутствия)
- Market-derived features (implied prob по другим рынкам)

### Уровень 3: Калибровка — калибруемся на шум

Калибровка на outcomes (матч выиграл/проиграл) — шумный сигнал. Один матч — одно событие. Pinnacle closing line — агрегированная мудрость рынка, доказано калиброванная с ошибкой 0.02% на 31,550 матчах. Это должна быть наша calibration target.

---

## Roadmap: 5 фаз

### Фаза 1: Data foundation (2-3 недели)
**Цель**: Собрать данные которых не хватает, без изменения модели.

1. **Historical standings backfill** — standings_delta = 0 во всех training data. Это убивает одну из 13 features стэкинга. Backfill standings per matchday для всех исторических сезонов.

2. **Odds snapshots pipeline** — убедиться что собираем: opening odds, T-24h odds, T-6h odds, T-1h odds, closing odds (before kickoff). Сейчас есть odds_snapshots но непонятно сколько точек per fixture.

3. **Pinnacle odds** — если API-Football даёт Pinnacle odds отдельно от avg market odds, начать сохранять. Если нет — исследовать альтернативные источники (football-data.co.uk даёт бесплатные Pinnacle closing).

4. **xG per match context** — API-Football даёт total xG. Исследовать: доступны ли xG splits (1st/2nd half, home/away xG when score is level vs leading/trailing)?

**Deliverable**: Расширенная БД с историческими standings, odds timeline, Pinnacle data.

### Фаза 2: COM-Poisson base model (3-4 недели)
**Цель**: Заменить Poisson на COM-Poisson в Dixon-Coles.

Исследования однозначны: голы underdispersed. COM-Poisson(λ, ν) где ν > 1 — правильная маргинальная спецификация.

1. **Реализация CMP-DC**: Conway-Maxwell-Poisson PMF вместо Poisson PMF. Главная сложность — нормировочная константа Z(λ,ν) не имеет closed form, нужна numerical truncation.

2. **ν как функция competitive balance**: ν = ν₀ + ν₁·|α_i − β_j|. При равных командах ν ≈ 1 (Poisson), при мисматче ν > 1 (underdispersion). Один дополнительный параметр.

3. **Расширенная τ-коррекция**: вместо 4 ячеек (0,0)/(0,1)/(1,0)/(1,1), расширить до (2,0)/(0,2)/(2,1)/(1,2)/(2,2) по Michels et al. Sarmanov с параметром s=2 или s=3.

4. **Team-specific γ_i**: иерархическая структура home advantage. γ_i ~ N(γ₀, σ²_γ) — каждая команда имеет свой HA, регуляризованный к среднему.

5. **Ablation**: CMP-DC vs standard DC на 7604 исторических матчах. Метрика: RPS, LogLoss.

**Deliverable**: Новый CMP-Dixon-Coles с per-team HA и расширенной зависимостью. Ablation доказывает improvement.

### Фаза 3: Feature engineering для стэкинга (2-3 недели)
**Цель**: Расширить feature vector стэкинга с 13 до 25-30 features.

Новые features:

**A. Market features**:
- `odds_movement`: (closing_implied_prob - opening_implied_prob). Движение линии = smart money signal
- `market_disagreement`: разница между нашей prob и implied closing prob
- `overround`: маржа букмекера для этого матча (высокий overround → менее ликвидный рынок)

**B. Performance features**:
- `xg_diff_l5`: rolling 5-match xG difference (уже считаем, но не используем в стэкинге)
- `xg_overperformance_l10`: (actual goals - xG) за 10 матчей. Positive = "перевыполняет" xG, negative = underperformance. Известный mean-reversion signal
- `form_trend`: slope линейной регрессии последних 10 результатов (тренд формы, не абсолют)
- `ppg_l5_home / ppg_l5_away`: points per game дома/в гостях за 5 матчей

**C. Context features**:
- `days_since_last_match_home / away`: более гранулярная версия rest_hours
- `match_importance`: position-based proxy (борьба за чемпионство, вылет, еврокубки)
- `h2h_home_advantage`: историческое HA для данной пары команд (если > 5 матчей)

**D. CMP-DC features** (из Фазы 2):
- `p_home_cmp`, `p_draw_cmp`, `p_away_cmp` — CMP-based probs заменяют текущие DC probs
- `nu_home`, `nu_away` — dispersion параметры (индикатор competitive balance)

**E. standings_delta** — наконец работающий (после backfill в Фазе 1)

Всё это поступает на вход стэкинга. Retrain на расширенных training data.

**Deliverable**: Стэкинг v3 с 25-30 features. Ablation vs v2.

### Фаза 4: Calibration на Pinnacle closing (1-2 недели)
**Цель**: Перекалибровать финальные вероятности используя Pinnacle closing line как target.

Вместо Dirichlet calibration на outcomes (шумно, нужно 500+ примеров), обучить calibrator на:
- Input: наши post-stacking probs
- Target: Pinnacle closing implied probs (devigged)

Pinnacle closing на 31,550+ матчах показывает calibration error 0.02%. Это quasi-ground-truth. Если наш calibrator научится маппить наши probs на Pinnacle probs, мы:
1. Получаем хорошо калиброванные probs (→ Kelly становится безопасен)
2. Можем находить value в soft books (bet365, 1xBet), чьи линии менее эффективны

**Deliverable**: Calibrator trained на Pinnacle closing. Calibration error < 0.03.

### Фаза 5: Bet selection и execution (1-2 недели)
**Цель**: Оптимизировать когда и где ставить.

1. **Timing**: Оптимальное время ставки. Early (T-48h) линии имеют больше value но менее стабильны. Late (T-2h) линии эффективнее. Исследовать оптимальный timing через CLV analysis.

2. **Soft books targeting**: Если модель откалибрована на Pinnacle, ищем value в soft books. Каждый soft book имеет разный margin и efficiency.

3. **Kelly sizing**: При calibration error < 0.03 — активировать fractional Kelly (1/4 Kelly для начала).

4. **Correlation-aware portfolio**: Вместо independent bets — учитывать корреляцию между ставками на один матч (1X2 + TOTAL) и between-match correlation в accumulator-подобных сценариях.

**Deliverable**: Optimized bet selection + Kelly sizing. CLV > 0 подтверждён.

---

## Ожидаемый эффект каждой фазы

| Фаза | Что даёт | Ожидаемый ΔRPS | Время |
|------|----------|----------------|-------|
| 1. Data foundation | standings_delta работает, odds timeline | -0.005 to -0.01 (stacking features) | 2-3 нед |
| 2. CMP-DC | Правильная спецификация base model | -0.01 to -0.02 (underdispersion fix) | 3-4 нед |
| 3. Feature engineering | Market + performance signals | -0.005 to -0.015 | 2-3 нед |
| 4. Pinnacle calibration | Kelly-safe probs, soft book value | calibration error < 0.03 | 1-2 нед |
| 5. Bet execution | CLV > 0, profitable operation | ROI +2-5% (target) | 1-2 нед |

**Суммарно**: 10-14 недель. Target: RPS < 0.175, calibration < 0.03, CLV > 0, ROI > 0.

---

## Что НЕ делать (и почему)

- **Neural networks / deep learning** — не хватит данных. 7604 матча — мало для DL. DC с правильной спецификацией будет лучше.
- **Player-level модели** — player Elo/ratings не добавляют value поверх team-level (D-number из DECISIONS.md). Lineup data полезно как binary feature (ключевой игрок отсутствует), но не как полная player-level модель.
- **Больше лиг** — пока не работает на 5 лигах, добавление новых только размывает фокус.
- **In-play betting** — другая задача, другая инфраструктура, другие данные.
- **Accumulator/parlay optimization** — требует решённой single-bet profitability.

---

## Порядок реализации

Фазы 1 и 2 можно начать параллельно:
- Фаза 1 (data) — может делать backend-агент
- Фаза 2 (CMP-DC) — исследовательская, нужен архитектор

Фаза 3 зависит от Фазы 1 (нужны данные) и Фазы 2 (нужны CMP probs).
Фаза 4 зависит от Фазы 3.
Фаза 5 зависит от Фазы 4.

```
Неделя 1-3:   [Фаза 1: data]──────────────┐
              [Фаза 2: CMP-DC]──────────┐  │
Неделя 4-6:                             ├──┤
                                        │  │
Неделя 7-9:   [Фаза 3: features]───────┘  │
                                           │
Неделя 10-11: [Фаза 4: calibration]───────┘
Неделя 12-14: [Фаза 5: execution]
```
