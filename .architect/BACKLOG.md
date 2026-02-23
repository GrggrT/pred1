# Backlog

Проблемы, идеи и TODO, обнаруженные в процессе работы, но НЕ входящие в текущую задачу.
Lead просматривает этот файл и решает, что приоритизировать.

Формат: `[дата] [severity: critical/high/medium/low] — описание`

---

## === Post-Task 17 (новые рынки) — 2026-02-22 ===

~~[2026-02-22] [critical] — `ENABLE_TOTAL_BETS` default=False~~ **RESOLVED** (Task 18): Изменено на True.

~~[2026-02-22] [high] — Kelly criterion НЕ применяется к вторичным рынкам~~ **RESOLVED** (Task 18): Kelly добавлен в _process_secondary_markets(), записывается в decision_payload.

~~[2026-02-22] [high] — BTTS и TOTAL в разных correlation groups~~ **RESOLVED** (Task 18, D028): Объединены в группу "goals".

~~[2026-02-22] [high] — Dashboard KPIs только 1X2~~ **RESOLVED** (Task 18): UNION без фильтра market, + bug fix selection_code→selection.

~~[2026-02-22] [medium] — History endpoint только 1X2~~ **RESOLVED** (уже было реализовано с market filter + UNION).

[2026-02-22] [medium] — Public site match modal показывает odds только для 1X2 и O/U 2.5. Новые odds (O/U 1.5, O/U 3.5, BTTS, DC) не отображаются, хотя данные есть в DB. Действие: расширить /api/public/v1/matches/{id} + UI modal.

~~[2026-02-22] [low] — test_elo_adjustment.py ImportError~~ **RESOLVED** (Task 18): Удалён.

[2026-02-22] [low] — Market columns hardcoded в 40+ местах по 6 файлам (sync_data, build_predictions, evaluate_results, quality_report, main.py, publishing). Нет централизованного markets.py. Добавление нового рынка требует правок в 15+ паттернах. Кандидат на рефакторинг при добавлении следующего рынка.

## === Pre-Task 17 ===

[2026-02-22] [medium] — SKIP rate 80%: 18/46 stacking SKIPs из-за odds_out_of_range. 8 из них имели EV > 0. Рассмотреть расширение MIN_ODD=1.30 (текущий 1.50) для сильных фаворитов, и MAX_ODD=4.00 (текущий 3.20) для ничьих. Мониторить ROI по odds buckets после 50+ settled.

[2026-02-22] [low] — EPL/Ligue1 EV threshold override 0.12 — агрессивный. Отсекает ~12 predictions с EV 0.03-0.12. Рассмотреть снижение до 0.08 после подтверждения calibration quality на 50+ settled.

~~[2026-02-22] [low] — scripts/train_model.py moved to scripts/deprecated/~~ **RESOLVED** (Task 18): scripts/deprecated/ удалён целиком.

~~[2026-02-22] [low] — build_predictions_legacy.py backup exists~~ **RESOLVED** (Task 18): Удалён.

[2026-02-22] [high] — Historical standings data for hist_fixtures: standings_delta = 0 currently in all historical data, reducing stacking quality. Need to backfill standings snapshots for historical seasons to improve stacking model training.

[2026-02-22] [medium] — Pinnacle closing line as calibration target: research suggests using Pinnacle closing lines as ground truth for calibration instead of match outcomes. Could improve calibration quality, especially for Dirichlet post-stacking.

[2026-02-22] [low] — Cache DC refits in ablation_study.py: currently DC is refit from scratch for each ablation config, even when configs share the same DC parameters. Caching DC fits per league/date would avoid duplicate computation and speed up ablation runs significantly.

~~[2026-02-22] [high] — DC rho на границе grid~~ **RESOLVED** (Task 18, D029): Grid расширен [-0.35, 0.35] / 71 steps. Нужен refit для проверки.

[2026-02-22] [medium] — Bundesliga: DC neutral/slightly worse (ΔRPS=+0.0007). Единственная лига из 6, где DC не улучшает baseline. Возможные причины: 18 команд (меньше data per team), высокий turnover. Требует investigation перед production-включением.

[2026-02-22] [medium] — Cross-league ablation невозможен текущей архитектурой: ablation_study.py фитит DC globally (все лиги вместе), что даёт 116-133 команды и ~80 сек per refit вместо ~3 сек per league. DC по определению не может оценить relative strength команд из разных лиг (нет общих матчей). Нужен per-league DC fitting в ablation.

[2026-02-22] [low] — Settled predictions: только 34 (23L, 11W, 3301 VOID). Недостаточно для stacking training (нужно ~100+). VOID predictions — возможно, результат backfill или legacy данных. Нужно проверить причину VOID.

[2026-02-22] [low] — Match indices xG coverage: 78 из 3357 (2.3%). Возможно, compute_indices job не вычисляет xG L5 корректно или xG данные появились позже.

~~[2026-02-21] [high] — Pillow (PIL) отсутствует в .venv~~ **RESOLVED** (сессия 9): Pillow>=10.4.0 добавлен в requirements.txt, установлен в venv.

[2026-02-21] [medium] — Дублирование кода TOTAL market в build_predictions.py: блок тоталов повторяется в двух местах (SKIP-путь строки ~1020-1070 и основной путь строки ~1240-1360). Кандидат на рефакторинг.

~~[2026-02-21] [medium] — Дублирование Poisson/Elo в scripts/~~ **RESOLVED** (сессия 9): Создан app/services/math_utils.py — shared float-based Poisson/Elo. backtest.py, train_model.py, ablation_study.py импортируют оттуда.

[2026-02-22] [low] — Rest hours fatigue neutral в ablation EPL (ΔRPS +0.0003 vs DC alone). Может быть полезнее в лигах с congested schedule или при использовании более точных rest_hours из match_indices. Мониторить performance в production.

[2026-02-22] [low] — Kelly criterion ENABLE_KELLY=false. Для активации: (1) накопить 200+ settled predictions, (2) проверить calibration quality, (3) включить. Kelly-adjusted ROI в evaluate_results — не реализован (можно добавить позже при активации Kelly).

[2026-02-21] [low] — build_predictions.py содержит EV-формулу `prob * odd - 1` где prob — модельная вероятность. Overround removal добавлен как диагностика (fair implied probs). При переходе на стэкинг (фаза 2) fair probs можно будет использовать как фичу для мета-модели.

[2026-02-21] [medium] — DC кэш в build_predictions.py загружает params per league+season, но не per-date. Если в backtest mode build_predictions обрабатывает fixtures с разными датами, все получат одинаковые DC params (последние на момент загрузки). Для walk-forward backtest нужен per-date кэш. Пока DC core не включён для backtest, это не критично.

[2026-02-21] [low] — tune_xi() доступен как функция, но не как job или API endpoint. Для удобства стоит создать scripts/tune_dc_xi.py или добавить опцию в fit_dixon_coles job (например, FIT_DC_TUNE_XI=true).

[2026-02-21] [low] — Standings delta и injury penalty в build_predictions.py применяются одинаково для обоих путей (DC и legacy). Для DC-пути это может создать small double-counting (DC уже видит матчи этих команд). Рассмотреть отключение standings/injuries при DC core.

~~[2026-02-21] [medium] — RPS в quality_report.py возвращает 0.0 (placeholder).~~ **RESOLVED** (Task 14): BetRow расширен feature_flags + home_goals/away_goals. _calibration() вычисляет RPS из полного распределения.

[2026-02-21] [low] — После активации новых Elo параметров (HA, goal-diff, regression) рекомендуется запустить force_recompute для пересчёта всех рейтингов с нуля. Текущие рейтинги в БД вычислены без этих улучшений.

[2026-02-21] [medium] — Stacking: scikit-learn — runtime dependency только в scripts/train_stacking.py. В app/ используется только numpy (StackingModel). Но scikit-learn добавлен в requirements.txt для удобства — при желании можно вынести в отдельный requirements-scripts.txt.

[2026-02-21] [medium] — Stacking training data: build_predictions теперь записывает p_home/draw/away_poisson и p_home/draw/away_dc в feature_flags. Однако для существующих (старых) predictions эти поля отсутствуют. train_stacking.py пропускает строки без base model probs. Для полноценного обучения нужно дождаться накопления ~100+ predictions с новыми feature_flags.

[2026-02-21] [low] — Stacking: logistic model probs не включены в stacking features (не записываются в feature_flags). При необходимости можно добавить p_home/draw/away_logistic как дополнительные фичи.

[2026-02-21] [medium] — Dirichlet calibrator: per-league calibration не реализована в pipeline (train_calibrator.py поддерживает --league-id, но build_predictions загружает calibrator один раз globally). Для per-league нужно кэшировать калибраторы per league_id в build_predictions.

[2026-02-21] [low] — Dirichlet: при включении USE_DIRICHLET_CALIB power scaling полностью заменяется. Если нужен conditional fallback (Dirichlet для одних лиг, power для других) — потребуется доработка.
