# Plan modernizacii kodovoj bazy / План модернизации кодовой базы

> Дата аудита: 2026-02-26
> Состояние: baseline-период, scheduler стабилен, данные копятся
> Приоритет: выполнять ПОСЛЕ сбора baseline-метрик (ориентир — март 2026)

---

## Оглавление

1. [Сводка текущего состояния](#1-сводка-текущего-состояния)
2. [Фаза 1 — Критичный рефакторинг](#2-фаза-1--критичный-рефакторинг)
3. [Фаза 2 — Архитектурные улучшения](#3-фаза-2--архитектурные-улучшения)
4. [Фаза 3 — Качество и DX](#4-фаза-3--качество-и-dx)
5. [Фаза 4 — Опциональные улучшения](#5-фаза-4--опциональные-улучшения)
6. [Что НЕ нужно менять](#6-что-не-нужно-менять)
7. [Метрики для оценки прогресса](#7-метрики-для-оценки-прогресса)

---

## 1. Сводка текущего состояния

| Область            | Оценка | Объём кода   | Главная проблема                          |
|--------------------|--------|--------------|-------------------------------------------|
| Backend-структура  | B+     | ~14K строк   | main.py — монолит (4,900 строк)           |
| Фронтенд          | B      | ~16K строк   | 3 приложения дублируют логику, нет TS      |
| База данных        | B-     | 56 миграций  | Нет ORM-моделей, весь SQL сырой            |
| Тестирование       | B      | 261+ тестов  | Нет frontend-тестов, нет coverage-метрик   |
| DevOps             | B+     | Docker x3    | Playwright-зависимость раздувает образ     |
| Зависимости        | A-     | 21 пакет     | Тяжёлые optional-зависимости не разделены  |
| Типизация          | B+     | ~70-80%      | Нет типов в raw SQL и во фронтенде         |
| Обработка ошибок   | B      | 146 try/except | Нет кастомных исключений                |
| Безопасность       | A-     | —            | Токен в localStorage, нет CORS-конфига     |
| DRY / организация  | B-     | 40+ дублей   | Маркеты захардкожены повсюду               |

**Общая зрелость: 7/10** — production-ready, но нужен рефакторинг для масштабируемости.

---

## 2. Фаза 1 — Критичный рефакторинг

> Приоритет: ВЫСОКИЙ | Срок: первым делом после baseline
> Цель: снизить риск поломок, упростить дальнейшую разработку

### 2.1. Разбить main.py на роутеры

**Проблема:** 4,900 строк, 50 роутов, scheduler, WebSocket, авторизация — всё в одном файле.

**Решение:** Использовать FastAPI `APIRouter`:

```
app/
├── main.py              → ~200 строк (startup, middleware, mount routers)
├── routers/
│   ├── __init__.py
│   ├── admin_api.py     → /api/v1/* (админские эндпоинты)
│   ├── public_api.py    → /api/public/v1/* (публичное API)
│   ├── jobs.py          → /api/v1/run-now, WebSocket, job-статусы
│   ├── ui.py            → Serving index.html, /admin, /ui
│   └── deps.py          → _require_admin(), rate limiters, общие зависимости
├── scheduler.py         → APScheduler setup, advisory locks
```

**Трудоёмкость:** 2-3 дня
**Риск:** Средний (много перемещений кода, нужны тесты)
**Выигрыш:** Каждый роутер меняется независимо, проще code review

### 2.2. Создать ORM-модели (Domain Layer)

**Проблема:** 141 вызов `text()` в main.py, директория `app/domain/` пустая. Нет проверки типов при обращении к колонкам БД.

**Решение:** SQLAlchemy Declarative модели:

```
app/domain/
├── __init__.py
├── base.py              → DeclarativeBase
├── fixture.py           → class Fixture(Base)
├── prediction.py        → class Prediction(Base), class PredictionTotal(Base)
├── odds.py              → class OddsSnapshot(Base)
├── team.py              → class Team(Base)
├── standing.py          → class Standing(Base)
├── job_run.py           → class JobRun(Base)
```

**Трудоёмкость:** 3-4 дня
**Риск:** Низкий (модели маппятся на существующие таблицы, SQL менять не обязательно сразу)
**Выигрыш:** Type safety, автодополнение в IDE, основа для перехода с raw SQL на ORM-запросы

### 2.3. Централизовать конфигурацию маркетов

**Проблема:** Конфиги 6 рынков (1X2, TOTAL, TOTAL_1_5, TOTAL_3_5, BTTS, DOUBLE_CHANCE) дублируются в 40+ местах: `build_predictions.py`, `main.py`, `admin.js`, `public.js`, тесты.

**Решение:**

```python
# app/core/markets.py
from dataclasses import dataclass
from typing import List

@dataclass(frozen=True)
class Selection:
    code: str
    label_ru: str
    label_en: str
    prob_key: str
    odds_col: str

@dataclass(frozen=True)
class MarketConfig:
    key: str
    label_ru: str
    label_en: str
    line: float | None
    correlation_group: str | None
    selections: List[Selection]

MARKETS: dict[str, MarketConfig] = { ... }

# + API-эндпоинт GET /api/public/v1/markets для фронтенда
```

**Трудоёмкость:** 1-2 дня
**Риск:** Низкий
**Выигрыш:** Добавление нового маркета — изменение в 1 файле вместо 40+

---

## 3. Фаза 2 — Архитектурные улучшения

> Приоритет: СРЕДНИЙ | Срок: после Фазы 1
> Цель: улучшить поддерживаемость крупных модулей

### 3.1. Разбить publishing.py (4,400 строк)

**Текущее состояние:** Генерация картинок + Telegram API + DeepL перевод — в одном файле.

**Целевая структура:**

```
app/services/
├── publishing/
│   ├── __init__.py          → публичный API модуля
│   ├── telegram.py          → отправка в Telegram
│   ├── image_generator.py   → Playwright → скриншот
│   ├── templates/           → HTML-шаблоны для картинок
│   └── translator.py        → DeepL-интеграция
```

**Трудоёмкость:** 2-3 дня
**Риск:** Средний (много внутренних зависимостей)

### 3.2. Упростить build_predictions.py (1,524 строки)

**Проблема:** Три пути предсказаний (DC+Stacking, DC-only, Poisson) с вложенной логикой.

**Решение:** Strategy pattern:

```python
# app/services/prediction_strategies.py
class PredictionStrategy(Protocol):
    async def predict(self, fixture, dc_params, ...) -> PredictionResult: ...

class StackingStrategy:    ...  # DC → Stacking → optional Dirichlet
class DCOnlyStrategy:      ...  # DC probabilities only
class PoissonStrategy:     ...  # Baseline Poisson
```

**Трудоёмкость:** 2-3 дня
**Риск:** Средний (ядро pipeline, нужно аккуратно)

### 3.3. Создать Repository-слой

**Проблема:** Роуты напрямую выполняют SQL-запросы. Одни и те же SELECT-ы дублируются.

**Решение:**

```
app/repositories/
├── __init__.py
├── fixtures.py      → get_by_id(), get_upcoming(), get_by_date_range()
├── predictions.py   → get_for_fixture(), get_settled(), get_with_value()
├── odds.py          → get_latest_snapshot(), get_pre_kickoff()
├── teams.py         → get_by_id(), get_with_elo()
```

**Трудоёмкость:** 3-4 дня (после создания ORM-моделей)
**Риск:** Низкий
**Выигрыш:** Устранение дублированных SQL-запросов, упрощение тестирования

---

## 4. Фаза 3 — Качество и DX

> Приоритет: НИЗКИЙ-СРЕДНИЙ | Срок: по мере необходимости
> Цель: улучшить developer experience и надёжность

### 4.1. Кастомная иерархия исключений

**Проблема:** Везде `Exception` или `HTTPException`. 146 блоков try/except в main.py, многие ловят `Exception` без конкретизации.

**Решение:**

```python
# app/core/exceptions.py
class AppError(Exception):
    """Базовое исключение приложения."""

class DataSyncError(AppError):
    """Ошибка синхронизации данных с API Football."""

class ModelFitError(AppError):
    """Ошибка при обучении модели (DC, Stacking)."""

class PredictionError(AppError):
    """Ошибка генерации предсказания."""

class PublishingError(AppError):
    """Ошибка публикации (Telegram, image gen)."""

# + @app.exception_handler(AppError) для единообразных ответов
```

**Трудоёмкость:** 1 день
**Риск:** Низкий

### 4.2. Frontend: общая библиотека утилит

**Проблема:** 3 JS-приложения (public.js, admin.js, ui.js) дублируют: API-клиент, localStorage, форматирование дат, `esc()`.

**Решение:**

```
app/shared/
├── tokens.css       → уже есть
├── utils.js         → esc(), formatDate(), formatNumber(), debounce()
├── api-client.js    → fetch-обёртка с auth, error handling, retry
```

**Трудоёмкость:** 1-2 дня
**Риск:** Низкий

### 4.3. Добавить coverage-метрики в CI

**Проблема:** 261+ тестов, но неизвестно какие модули покрыты.

**Решение:**

```yaml
# .github/workflows/ci.yml
- run: pytest --cov=app --cov-report=term-missing --cov-fail-under=60
```

**Трудоёмкость:** 30 минут
**Риск:** Нулевой

### 4.4. Frontend-тесты (Playwright)

**Проблема:** 16K строк JS без тестов. Только один smoke-тест.

**Решение:** Критические user flows:
- Публичный сайт: загрузка, переключение вкладок, клик по Top Value
- Админ: авторизация, запуск задания, просмотр матча

**Трудоёмкость:** 2-3 дня
**Риск:** Низкий

---

## 5. Фаза 4 — Опциональные улучшения

> Приоритет: НИЗКИЙ | Срок: когда/если понадобится
> Цель: оптимизация, не влияющая на функциональность

### 5.1. Разделить optional-зависимости

**Проблема:** Playwright (~50MB), pandas, scikit-learn грузятся всегда.

**Решение:**

```toml
# pyproject.toml (миграция с requirements.txt)
[project.optional-dependencies]
ml = ["scikit-learn>=1.4", "pandas>=2.2"]
publishing = ["playwright>=1.49", "Pillow>=10.4"]
```

### 5.2. Миграция фронтенда на TypeScript

**Проблема:** 16K строк JS без типов.

**Решение:** Постепенная миграция, начиная с `public.js` (наименьший файл):
1. Добавить `tsconfig.json` с `allowJs: true`
2. Переименовать `.js` → `.ts` поочерёдно
3. Добавить типы для API-ответов

**Трудоёмкость:** 3-5 дней на весь фронтенд
**Альтернатива:** JSDoc-аннотации (менее трудоёмко, даёт проверку типов в IDE)

### 5.3. Оптимизация Docker-образа

**Текущее:** Один образ для app и scheduler, включает Playwright.

**Решение:** Multi-stage build или отдельные образы:
- `app` — только API (без Playwright, pandas)
- `scheduler` — с ML-зависимостями
- `renderer` — отдельный сервис для генерации картинок

### 5.4. CORS и security hardening

- Явный `CORSMiddleware` с whitelist origins
- Миграция токена из localStorage в HttpOnly cookie
- Rate limiting через Redis (вместо in-memory dict)
- Request signing для публичного API

---

## 6. Что НЕ нужно менять

Эти части кодовой базы уже соответствуют современным практикам:

| Компонент | Почему не трогать |
|-----------|-------------------|
| `app/core/config.py` | Pydantic BaseSettings — правильный паттерн |
| `app/core/db.py` | Async SQLAlchemy 2.0, connection pooling — норма |
| `app/jobs/` модульность | Каждый job — отдельный файл с `run(session)` — чисто |
| `app/services/` (кроме publishing) | Хорошее разделение ответственности |
| `app/shared/tokens.css` | CSS Custom Properties — современный подход |
| Docker: отдельный scheduler | Правильная архитектура для multi-replica |
| CSP headers + XSS-защита | Безопасность на хорошем уровне |
| APScheduler + advisory locks | Надёжная защита от race conditions |
| Legacy UI (`app/ui/`) | DO NOT MODIFY — работает, используется |

---

## 7. Метрики для оценки прогресса

### До начала рефакторинга (baseline)
- [ ] `main.py`: 4,900 строк
- [ ] `publishing.py`: 4,400 строк
- [ ] Сырых SQL-запросов (`text()`): ~220+
- [ ] ORM-моделей: 0
- [ ] Маркет-дублирований: 40+
- [ ] Frontend coverage: 0%
- [ ] Backend coverage: неизвестно

### Целевые показатели после всех фаз
- [ ] `main.py`: < 300 строк
- [ ] Самый большой файл: < 800 строк
- [ ] Сырых SQL-запросов: < 50 (остальное через ORM)
- [ ] ORM-моделей: 7+ (все основные таблицы)
- [ ] Маркет-конфиг: 1 файл (`markets.py`)
- [ ] Backend coverage: > 70%
- [ ] Frontend coverage: критические flows

---

## Порядок выполнения (roadmap)

```
Март 2026 — Сбор baseline-метрик (НЕ ТРОГАТЬ КОД)
    │
    ▼
Фаза 1 (1-2 недели)
    ├── 2.1 Разбить main.py на роутеры
    ├── 2.3 Централизовать маркеты
    └── 2.2 Создать ORM-модели
    │
    ▼
Фаза 2 (1-2 недели)
    ├── 3.1 Разбить publishing.py
    ├── 3.2 Strategy pattern для predictions
    └── 3.3 Repository-слой
    │
    ▼
Фаза 3 (по мере необходимости)
    ├── 4.1 Кастомные исключения
    ├── 4.2 Shared JS utils
    ├── 4.3 Coverage в CI
    └── 4.4 Frontend-тесты
    │
    ▼
Фаза 4 (опционально)
    ├── 5.1 Optional dependencies
    ├── 5.2 TypeScript
    ├── 5.3 Docker optimization
    └── 5.4 Security hardening
```
