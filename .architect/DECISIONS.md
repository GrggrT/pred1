# Архитектурные решения

Каждое нетривиальное решение фиксируется здесь с обоснованием, чтобы Lead мог проверить логику.

Формат:
## [ID] Название решения
- **Контекст**: Почему возник вопрос
- **Варианты**: Что рассматривалось
- **Решение**: Что выбрано
- **Обоснование**: Почему
- **Статус**: Принято / На согласовании с Lead

---

## [D029] DC rho grid expansion [-0.35, 0.35] / 71 steps
- **Контекст**: EPL, Bundesliga, La Liga показывали rho=-0.20 — точно на границе grid [-0.2, 0.2]. Оптимальный rho мог лежать за пределами.
- **Варианты**: (1) Расширить до [-0.3, 0.3], (2) Расширить до [-0.35, 0.35], (3) Adaptive grid search
- **Решение**: Вариант 2 — [-0.35, 0.35] / 71 step (шаг 0.01). Достаточно широко, вычислительная стоимость +73% (71 vs 41), но каждый grid step < 1 sec.
- **Обоснование**: DC literature rho обычно в [-0.15, -0.05]. rho=-0.35 — экстремальное значение, если после refit оно окажется на новой границе — это сигнал о проблемах с данными.
- **Статус**: Принято. Мониторить rho после refit.

## [D028] BTTS + TOTAL → unified "goals" correlation group
- **Контекст**: BTTS group="btts" и TOTAL group="total" были независимы. max_total_bets_per_fixture=1 ограничивало только TOTAL линии, позволяя BTTS + TOTAL одновременно на одном матче. Но они математически коррелированы: P(BTTS_YES) ≈ P(OVER_1.5) − P(exactly 2-0 or 0-2).
- **Варианты**: (1) Оставить раздельные группы, (2) Объединить в "goals", (3) Объединить + отдельный max_btts_per_fixture
- **Решение**: Вариант 2 — единая группа "goals" (TOTAL 1.5/2.5/3.5 + BTTS), max 1 bet per fixture из этой группы.
- **Обоснование**: Простота. DC в отдельной группе (коррелирует с 1X2, который обрабатывается отдельно). Один goals-bet — консервативная стратегия для начала, можно расширить max_total_bets_per_fixture позже.
- **Статус**: Принято

---

## [D025] Рефакторинг на единый prediction pipeline
- **Контекст**: build_predictions.py содержал 6+ code paths (Poisson, logistic, hybrid, DC, stacking, power scaling), накопленных за время эволюции модели. Только один path (DC→Stacking) используется в production.
- **Варианты**:
  1. Оставить legacy paths с deprecated warnings
  2. Удалить всё кроме production pipeline + два fallback
- **Решение**: Вариант 2 — единый pipeline DC → Stacking → optional Dirichlet
- **Обоснование**: Legacy paths (hybrid, logistic, power scaling) доказано уступают стэкингу (D010, D021). Их наличие создаёт: (a) cognitive load при чтении кода, (b) risk случайной активации через env flags, (c) impedance при рефакторинге (каждое изменение затрагивает 6 paths). Backup сохранён как `build_predictions_legacy.py`.
- **Результат**: -201 строк, -6 config flags, 116 тестов проходят. Production pipeline работает.
- **Статус**: Принято

## [D024] Stacking v2: DC-xG features — neutral RPS, model saved
- **Контекст**: DC-xG (Task 12) validated via ablation (ΔRPS=-0.0095 vs DC-goals -0.0085). Added 3 DC-xG features to stacking (13 total, removed dead standings_delta).
- **Данные**: training_data_v2.json: 7308 samples, 6183 (84.6%) with DC-xG. Train=5846, Val=1462.
- **Результат**: Val RPS v2=0.1887 vs v1=0.1886 (Δ=+0.0001, статистически незначимо). DC-xG coefficients non-zero (p_away_dc_xg: H=-0.69, A=+0.61).
- **Решение**: Model saved. DC-xG adds independent signal. With more data (full season), benefit should materialize. No downside risk.
- **Статус**: Принято

## [D023] τ(ρ) при xG → rho=0, skip grid search
- **Контекст**: Dixon-Coles tau correction defined for integer scores. With fractional xG, tau loses meaning.
- **Варианты**: A) rho=0, skip grid search. B) Round xG to integers for tau.
- **Решение**: Variant A — rho=0.0, single optimization pass. DC-xG fit ~20-40x faster (0.1-0.5s vs 1.5-7s).
- **Обоснование**: Quasi-Poisson kernel `y*log(λ) - λ` (without log-factorial for fractional y). Tau is a low-score correction that makes no sense with continuous xG.
- **Статус**: Принято

## [D022] Fair odds are the strongest stacking feature
- **Контекст**: Feature importance from stacking model shows fair_away (+1.46), fair_draw (+1.05), fair_home (+0.96) dominate
- **Решение**: Record insight. Implies bookmaker pricing is highly informative and should always be available.
- **Обоснование**: This is expected — bookmakers aggregate enormous information. When odds are missing (fair_home=0), stacking falls back to DC/Poisson signal. standings_delta = 0 in historical data (gap in data pipeline).
- **Статус**: Принято

## [D021] Dirichlet on post-stacking probs — marginal improvement
- **Контекст**: Ablation config 3 vs config 2: ΔRPS = -0.0018 globally, but inconsistent across leagues (helps La Liga/Portugal, hurts EPL)
- **Решение**: Don't activate USE_DIRICHLET_CALIB for now. Revisit when more data available.
- **Обоснование**: Inconsistent improvement across leagues suggests overfitting risk. Dirichlet helps in 2/3 leagues but hurts EPL. Global improvement (-0.0018) is within noise. Need more data to confirm stable benefit.
- **Статус**: Принято

## [D019] Walk-forward training data via generate_training_data.py
- **Контекст**: Для обучения стэкинга и калибратора нужны OOS предсказания. В DB только 34 settled predictions, но 7604 исторических матчей.
- **Варианты**:
  1. Ждать 100+ settled predictions (месяцы)
  2. Ретроспективная walk-forward генерация из исторических данных
- **Решение**: Вариант 2 — generate_training_data.py
- **Обоснование**: Walk-forward гарантирует OOS (для каждого матча используются только данные до него). DC рефитится каждые 30 матчей (баланс точность/скорость). 7308 примеров из 6 лиг — достаточно для стэкинга. psycopg2 (sync) для скорости, не asyncpg.
- **Статус**: Принято

## [D020] Dirichlet калибровка на DC probs нецелесообразна
- **Контекст**: Dirichlet calibrator обучен на DC probs (7225 примеров). Результат: ΔLogLoss=+0.4%, ΔRPS=+1.0% — ухудшение.
- **Варианты**:
  1. Активировать Dirichlet на DC probs (ухудшает метрики)
  2. Не применять Dirichlet на DC (DC уже калиброван)
  3. Применять Dirichlet ПОСЛЕ стэкинга (D013)
- **Решение**: Вариант 2/3 — Dirichlet на DC не нужен, рассмотреть после активации стэкинга
- **Обоснование**: DC-модель с walk-forward refitting уже производит хорошо калиброванные вероятности. Dirichlet добавляет noise вместо коррекции. Согласно D013, Dirichlet должен применяться к финальным (post-stacking) вероятностям.
- **Статус**: Принято

## [D018] Дедупликация: shared math_utils.py вместо замены float→Decimal
- **Контекст**: Scripts (backtest, train_model, ablation) используют float Poisson/Elo, app/ — Decimal. Можно: (a) заставить scripts импортировать Decimal-версии из app/services/poisson.py, (b) создать shared float-модуль.
- **Решение**: Вариант (b) — `app/services/math_utils.py` с float-based canonical implementations
- **Обоснование**: Scripts работают с float для скорости (numpy, scipy). Decimal-версии в poisson.py на ~100x медленнее. Принудительное использование Decimal в offline-скриптах нецелесообразно. math_utils.py — единый источник float-логики, poisson.py — единый источник Decimal-логики.
- **Статус**: Принято

## [D017] requirements.lock через pip-compile
- **Контекст**: Нужен lock-файл для воспроизводимых сборок.
- **Варианты**: (a) pip-compile (pip-tools), (b) uv pip compile, (c) poetry
- **Решение**: pip-compile → requirements.lock
- **Обоснование**: pip-tools уже совместим с pip, не требует миграции на poetry/uv. Lock-файл используется в Dockerfile. requirements.txt остаётся для разработки (loose constraints).
- **Статус**: Принято

## [D016] CI workflow: alembic migrations before tests
- **Контекст**: Тесты с `api_client` fixture требуют реальную DB. Без миграций DB пустая.
- **Решение**: `alembic upgrade head` перед `pytest` в CI workflow
- **Обоснование**: conftest.py скипает тесты при unreachable DB, но если DB доступна (CI Postgres service) — нужны таблицы. Без миграций api_client тесты пройдут, но вернут 500 вместо expected results.
- **Статус**: Принято

## [D015] Kelly criterion default OFF
- **Контекст**: Kelly criterion реализован для bet sizing, но модель ещё не доказала calibration quality.
- **Варианты**:
  1. Включить сразу — рискованно при некалиброванных вероятностях
  2. Выключить по default, включить после 200+ settled predictions
- **Решение**: Вариант 2 — `ENABLE_KELLY=false`
- **Обоснование**: Quarter Kelly (fraction=0.25) с max_fraction=0.05 снижает risk, но при miscalibrated probs Kelly ещё усиливает ошибку. Включать только когда calibration quality доказана (p_home/p_draw/p_away accuracy на validation set).
- **Статус**: Принято

## [D014] Fatigue factor piecewise linear curve
- **Контекст**: Выбор формы кривой fatigue(rest_hours) → lambda multiplier.
- **Варианты**:
  1. Step function (< 72h → 0.92, 72-120 → 0.97, else → 1.0)
  2. Piecewise linear (smooth transitions between intervals)
  3. Exponential decay/recovery
- **Решение**: Вариант 2 — piecewise linear
- **Обоснование**: Smooth transitions избегают discontinuities (шагов), которые могут вызвать нестабильность при граничных значениях. Кривая: <72h → [0.90, 0.95], 72-120h → [0.95, 1.00], 120-192h → [1.00, 1.02], >=192h → 1.00. Ablation на EPL: ΔRPS +0.0003 vs DC alone (neutral), что допустимо. В production данные rest_hours из match_indices (более точные чем вычисленные из дат).
- **Статус**: Принято

## [D011] Собственная реализация Dirichlet calibration vs пакет dirichletcal
- **Контекст**: Нужна мультиклассовая калибровка. Пакет dirichletcal существует, но core logic = ~50 строк.
- **Варианты**:
  1. `pip install dirichletcal` — готовый пакет
  2. Собственная реализация log(p) → W@log(p)+b → softmax на scipy.optimize
- **Решение**: Вариант 2 — собственная реализация
- **Обоснование**: (a) dirichletcal может иметь проблемы совместимости с numpy 2.x / Python 3.12+, (b) core logic — 12 параметров, L-BFGS-B + аналитический градиент, ~100 строк, (c) scipy уже в зависимостях, (d) полный контроль над регуляризацией и fallback. Риск: отсутствие extensive тестирования пакета. Митигация: 9 юнит-тестов на синтетике.
- **Статус**: Принято

## [D012] Default reg_lambda=0.01 для Dirichlet calibrator
- **Контекст**: Выбор силы регуляризации влияет на overfitting vs underfitting.
- **Варианты**: 1e-3 (слабая), 1e-2 (умеренная), 1e-1 (сильная)
- **Решение**: 1e-2 как default, настраиваемо через --reg-lambda
- **Обоснование**: При типичных размерах выборки (200-1000 predictions) 1e-2 даёт баланс: достаточно гибкости для коррекции bias по классам, но не overfits на noise. При маленьких выборках (<200) train_calibrator предупреждает. При очень маленьких (<30) — fallback на identity.
- **Статус**: Принято

## [D013] Порядок в pipeline: Base → Stacking → Dirichlet → EV
- **Контекст**: Dirichlet должен калибровать финальные вероятности. Если применить до стэкинга, стэкинг увидит уже калиброванные probs, что нарушает training assumptions.
- **Решение**: Dirichlet применяется ПОСЛЕ стэкинга (или single model), ВМЕСТО power scaling
- **Обоснование**: Стэкинг обучен на raw base model probs. Если дать ему калиброванные, возникнет distribution shift. Dirichlet — финальный post-processing перед EV calc. При отсутствии калибратора — fallback на legacy power scaling.
- **Статус**: Принято

## [D008] Walk-forward safety: Variant A (stored OOS predictions)
- **Контекст**: Для обучения мета-модели стэкинга нужны out-of-sample предсказания базовых моделей. Два варианта: (A) использовать predictions, которые build_predictions уже записал в feature_flags ДО матча, (B) для каждого матча в train set заново fit базовые модели на данных до этого матча.
- **Варианты**:
  1. Variant A — использовать feature_flags (p_home_poisson, p_home_dc и т.д.), записанные build_predictions при создании prediction. Эти предсказания по определению out-of-sample (сделаны до матча).
  2. Variant B — walk-forward refitting. Для каждого матча в training set заново вызывать fit_dixon_coles + Poisson на данных до этого матча. Вычислительно дорого (N × fit).
- **Решение**: Variant A для MVP
- **Обоснование**: Feature_flags записываются build_predictions ДО матча → это гарантированный OOS. Variant B даёт более чистые OOS-предсказания (нет information leakage через hyperparameters), но стоит O(N × fit_time). Для MVP Variant A достаточен. Variant B — фаза 3.
- **Статус**: Принято

## [D009] Хранение stacking коэффициентов в model_params.metadata JSONB
- **Контекст**: model_params имеет param_value NUMERIC(12,6) — не подходит для массивов (coefficients shape 3×N). Нужен формат хранения.
- **Варианты**:
  1. Отдельные строки per coefficient (scope='stacking', param_name='coef_0_0', param_value=...)
  2. Новая миграция с param_json TEXT column
  3. Использовать существующий metadata JSONB column
- **Решение**: Вариант 3 — metadata JSONB
- **Обоснование**: metadata JSONB уже существует в model_params. Одна строка: scope='stacking', param_name='model', metadata={coefficients, intercept, feature_names, n_samples, val_rps, val_logloss}. Не нужна миграция. Загрузчик `load_stacking_model` отдельный от `_load_model_params` (не ломает существующий код).
- **Статус**: Принято

## [D010] Stacking приоритетнее hybrid mode
- **Контекст**: При USE_STACKING=true и USE_HYBRID_PROBS=true — что использовать?
- **Решение**: USE_STACKING имеет приоритет. Hybrid mode игнорируется с warning в лог.
- **Обоснование**: Стэкинг — строго более мощная комбинация (нелинейная, обученная на OOS). Линейный пулинг provably breaks calibration. Нет смысла использовать оба.
- **Статус**: Принято

## [D001] Интеграция overround removal — диагностика vs замена EV формулы
- **Контекст**: Задание требует заменить `1/odd` на overround-нормализованные вероятности в build_predictions.py. Однако EV формула (`model_prob * bookie_odd - 1`) не использует implied probabilities — вероятности берутся из Poisson/DC/logistic модели.
- **Варианты**:
  1. Заменить EV формулу на `model_prob * fair_odd - 1` — завышает EV, приводит к ложным сигналам
  2. Добавить fair implied probs как диагностические метрики в feature_flags/decision payloads
  3. Использовать fair implied probs для threshold adjustment (dynamic threshold based on edge vs fair odds)
- **Решение**: Вариант 2 — добавить как диагностику
- **Обоснование**: EV формула корректно рассчитывает ожидаемый доход по реальным ценам букмекера. Fair implied probs полезны для: (a) оценки "true edge" = model_prob - fair_implied_prob, (b) будущего стэкинга (фаза 2) как фича, (c) калибровки порогов. Вариант 1 опасен — завышенный EV = убыточные ставки.
- **Статус**: Принято

## [D006] Детекция границы сезона через gap > 45 дней
- **Контекст**: Нужен механизм определения, когда применять regression-to-mean. Варианты: по дате (жёстко), по gap в кикоффах, по метаданным API (season field).
- **Варианты**:
  1. Фиксированная дата (1 июля для европейских лиг)
  2. Gap > N дней между последовательными матчами в обработке
  3. Сравнение поля season в fixtures
- **Решение**: Вариант 2 — gap > 45 дней
- **Обоснование**: Универсально работает для всех лиг и полушарий. Европейские лиги имеют летний перерыв ~2-3 месяца, южноамериканские — зимний. 45 дней достаточно для фильтрации международных пауз (~14 дней max) и рождественского перерыва (~21 дней). Не требует знания календаря лиги. Вариант 3 надёжнее, но fixtures могут содержать смешанные сезоны в одном batch.
- **Статус**: Принято

## [D007] RPS в quality_report — placeholder, не полноценный
- **Контекст**: quality_report работает с BetRow, который содержит только prob (confidence на выбранный selection), а не полное распределение p_home/p_draw/p_away. Для RPS нужно полное распределение.
- **Варианты**:
  1. Расширить BetRow и SQL-запрос для загрузки feature_flags (тяжёлый JOIN)
  2. Аппроксимировать distribution из selection + prob (ненадёжно)
  3. Вернуть rps=0 как placeholder, полноценный RPS — только в evaluate_results
- **Решение**: Вариант 3 — placeholder
- **Обоснование**: evaluate_results имеет доступ к feature_flags с p_home/p_draw/p_away и считает RPS корректно. quality_report предназначен для ROI/CLV/calibration анализа, где brier и logloss достаточны. Добавление тяжёлого JOIN для feature_flags в quality_report нецелесообразно до фазы 3.
- **Статус**: Принято

## [D002] DC fitting: ρ через grid search, не joint optimization
- **Контекст**: ρ можно оптимизировать совместно с att/def/HA или отдельно через grid search.
- **Варианты**:
  1. Joint optimization (ρ как ещё один параметр в L-BFGS-B)
  2. Grid search ρ ∈ [-0.2, 0.2] с 41 шагом, для каждого fit остальные параметры
- **Решение**: Вариант 2 — grid search
- **Обоснование**: ρ имеет сложный ландшафт (tau может дать отрицательные значения при больших ρ), joint optimization часто не сходится. Grid search надёжнее и позволяет warm-start между итерациями. 41 шаг × O(ms) per fit = незначительные доп. затраты. Это стандартный подход в литературе.
- **Статус**: Принято

## [D003] Elo adjustment не применяется поверх DC-пути
- **Контекст**: В legacy-пути Elo adjustment масштабирует λ/μ. В DC-пути λ/μ уже учитывают силу команд через латентные att/def.
- **Варианты**:
  1. Применять Elo adjustment поверх DC λ/μ (двойной учёт силы)
  2. Не применять Elo adjustment при DC core (силы уже в модели)
  3. Сделать конфигурируемым через отдельный флаг
- **Решение**: Вариант 2 — не применять
- **Обоснование**: DC-модель уже декомпозирует силу команд через att/def. Elo adjustment поверх — это двойной учёт одного и того же сигнала. Elo остаётся доступен как отдельная фича для стэкинга (фаза 2) и для диагностики в feature_flags.
- **Статус**: Принято

## [D004] Fallback при отсутствии DC params для команды/лиги
- **Контекст**: DC params могут отсутствовать (< 30 матчей, новая команда, первый запуск).
- **Варианты**:
  1. Raise error и skip fixture
  2. Fallback на legacy-путь (rolling averages)
  3. Использовать средние DC params для лиги
- **Решение**: Вариант 2 — graceful fallback на legacy
- **Обоснование**: build_predictions.py проверяет наличие DC params для обоих команд и глобальных параметров. Если любой отсутствует, `dc_core_used=False` и используется legacy-путь. Это гарантирует zero-downtime при первом запуске или при добавлении новых лиг.
- **Статус**: Принято

## [D005] ξ фиксирован (0.005), не тюнится per-league автоматически
- **Контекст**: Оптимальный ξ может отличаться per-league (EPL vs Serie A).
- **Варианты**:
  1. Автоматический tune_xi в каждом job run
  2. Фиксированный ξ=0.005 (default), tune_xi доступен для offline запуска
  3. Per-league ξ через env переменные
- **Решение**: Вариант 2 — фиксированный default, tune доступен
- **Обоснование**: tune_xi дорогой (многократный fit для каждого ξ). При 4 лигах × 10+ xi значений = 40+ fits per run. Лучше тюнить offline через скрипт и фиксировать найденное значение. ξ=0.005 ~ полупериод 140 дней — разумный default для большинства европейских лиг.
- **Статус**: Принято
