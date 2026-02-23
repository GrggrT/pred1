# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Docker Environment (Recommended)
```bash
# Full setup
docker compose up --build -d
docker compose exec -T app alembic upgrade head

# Rebuild after code changes
docker compose build --no-cache scheduler  # For job changes
docker compose build --no-cache app        # For API changes
docker compose restart scheduler

# Complete rebuild (clean everything)
docker compose down
docker system prune -f
docker image prune -a -f
docker volume prune -f
docker compose build --no-cache --pull
docker compose up -d

# Logs and debugging
docker compose logs app --tail=20
docker compose logs scheduler --tail=20

# Database access
docker compose exec app alembic upgrade head
docker compose exec db psql -U postgres -d fc_mvp
```

### Local Development
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ADMIN_TOKEN=dev
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Testing
```bash
pytest -q                              # Run all tests
pytest -v tests/test_specific.py       # Run specific test
pytest tests/test_elo_adjustment.py    # Test ELO calculations
pytest tests/test_status_mapping.py    # Test status mappings
python -m compileall -q app            # Syntax check
```

### Manual Job Execution
```bash
# Via API (requires ADMIN_TOKEN)
curl -H "X-Admin-Token: dev" -X POST "http://localhost:8000/api/v1/run-now?job=sync_data"
curl -H "X-Admin-Token: dev" -X POST "http://localhost:8000/api/v1/run-now?job=full_pipeline"
curl -H "X-Admin-Token: dev" -X POST "http://localhost:8000/api/v1/run-now?job=evaluate_results"

# Test specific jobs directly (inside scheduler container)
docker compose exec scheduler python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.jobs import evaluate_results
from app.core.config import settings

async def test():
    engine = create_async_engine(settings.database_url)
    SessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionLocal() as session:
        result = await evaluate_results.run(session)
        print(f'Result: {result}')
    await engine.dispose()

asyncio.run(test())
"
```

## Architecture Overview

### Core Components

**Data Pipeline**: Six-stage automated pipeline for football predictions:
1. `sync_data` - Fetch fixtures, odds, standings, injuries from API Football
2. `compute_indices` - Calculate team ratings (ELO, form, xG rolling averages)
3. `fit_dixon_coles` - Fit DC model per league (goals + xG dual-mode)
4. `build_predictions` - Generate ML predictions (DC → Stacking → EV calc)
5. `evaluate_results` - Settle predictions and calculate performance metrics
6. `maintenance` - Clean up old data and manage retention

**Scheduler Architecture**:
- Production uses separate `app` (API only) and `scheduler` (jobs only) services
- Prevents job racing in multi-replica deployments
- PostgreSQL advisory locks ensure single job execution
- APScheduler with cron triggers for automated execution

**Database Layer**:
- AsyncPG + SQLAlchemy 2.0 with async sessions
- Alembic migrations in `alembic/versions/`
- Key tables: `fixtures`, `predictions`, `odds_snapshots`, `teams`, `standings`
- Comprehensive foreign key relationships with cascading

### API Structure

**Main Application** (`app/main.py`):
- FastAPI with admin token authentication
- Embedded UI serving from `/ui/index.html`
- WebSocket support for real-time job status
- Rate-limited manual job triggers via `/api/v1/run-now`

**Job System** (`app/jobs/`):
- Each job is a standalone module with `run(session: AsyncSession)` function
- Shared utilities in `app/core/` (config, database, logging, time utils)
- Job status tracking and auditing in `job_runs` table
- Advisory locking prevents concurrent execution

**Services Layer** (`app/services/`):
- `dixon_coles.py` - Dixon-Coles model fitting (dual-mode: goals + xG)
- `elo_ratings.py` - ELO rating calculations (home advantage, goal-diff, regression)
- `stacking.py` - Stacking meta-model inference (softmax regression, 13 features)
- `calibration.py` - Dirichlet calibration (optional post-stacking)
- `poisson.py` - Poisson/DC probability calculations (Decimal-based)
- `kelly.py` - Kelly criterion bet sizing
- `odds_utils.py` - Overround removal for fair implied probabilities
- `metrics.py` - RPS, Brier, LogLoss scoring functions

**Prediction Pipeline** (`app/jobs/build_predictions.py`):
- Three-level model selection: Stacking (primary) → DC-only (fallback) → Poisson (baseline)
- DC always enabled, fit via `fit_dixon_coles` job (goals + xG modes)
- Stacking uses 13 features: 3 Poisson + 3 DC-goals + 3 DC-xG + elo_diff + 3 fair odds
- Optional Dirichlet calibration post-stacking (USE_DIRICHLET_CALIB)
- EV calculation: `model_prob * bookie_odd - 1` (unchanged)

### Frontend Architecture

**Single-Page Application** (`app/ui/index.html`):
- Vanilla JavaScript with custom CSS variables and modern design
- Logically grouped navigation: 📊 Обзор, 📈 Анализ, ⚙️ Управление
- Real-time updates via WebSocket connections
- Token-based authentication stored in localStorage
- Responsive design optimized for both mobile and desktop
- Consistent header patterns with refresh buttons across all sections

**Navigation Structure**:
- **📊 Обзор**: Главная (Dashboard KPIs) + Live Picks (current predictions)
- **📈 Анализ**: История ставок (ROI analysis) + Графики (charts/analytics)
- **⚙️ Управление**: Задания (job control) + База данных (data browser) + Компоненты (UI library)

**Key UI Functions**:
- `loadDashboardData()` - KPI metrics and recent activity with automatic updates
- `loadLiveData()` - Upcoming predictions with live odds (uses `sort=kickoff_desc`)
- `loadHistoryData()` - Betting history with ROI calculation (limit 20 records)
- `loadAnalyticsData()` - Historical betting analysis with charts
- `loadJobsData()` - Job execution interface with status monitoring

**UI Data Handling**:
- API field mappings: `pick.teams` for match display, not `pick.home`/`pick.away`
- Parallel API calls for dashboard (history + metrics)
- Error handling for "Loading..." states that never complete
- Real-time job status updates via WebSocket
- Consistent refresh button patterns across all sections

### Configuration & Environment

**Environment Setup**:
- `.env` file required (copy from `.env.example`)
- Key variables: `ADMIN_TOKEN`, `API_FOOTBALL_KEY`, `DATABASE_URL`
- League/season configuration: `LEAGUE_IDS`, `SEASON`, `BOOKMAKER_ID`
- Job scheduling via cron expressions (e.g., `JOB_SYNC_DATA_CRON=*/5 * * * *`)

**Backtest Modes**:
- `BACKTEST_KIND=pseudo` - Uses latest odds for historical analysis
- `BACKTEST_KIND=true` - Uses pre-kickoff odds snapshots for accurate backtesting
- Snapshot autofill system maintains coverage gaps

## Common Issues & Debugging

### Job Execution Problems
- Check scheduler logs: `docker compose logs scheduler --tail=50`
- Verify database connectivity and migrations
- **SQLAlchemy array parameter issues**: Use `IN ('val1', 'val2')` instead of `ANY(:param)` for static values, or `IN (SELECT unnest(CAST(:param AS integer[])))` for dynamic arrays
- Advisory lock conflicts: Jobs may appear stuck if previous run didn't release lock
- Transaction isolation errors: Ensure proper session handling and avoid nested transactions

### UI Issues
- **Authentication**: Ensure `X-Admin-Token` header matches `.env` `ADMIN_TOKEN`
- **API Calls**: Check browser network tab for 401/403 errors
- **Data Loading**: Most UI functions expect specific API field mappings (e.g., `pick.teams` vs `pick.home`/`pick.away`)

### Database Issues
- Migration conflicts: `docker compose exec app alembic upgrade head`
- Connection pool exhaustion: Restart database container
- Missing data: Run `sync_data` job to populate fixtures and odds

### Performance Considerations
- API Football rate limiting: Configured via `FETCH_RATE_MS`
- Database query optimization: Use appropriate indexes on date/status fields
- Memory usage: Large prediction datasets may require chunked processing

## Development Patterns

### Adding New Jobs
1. Create module in `app/jobs/` with `async def run(session: AsyncSession)` function
2. Add import to `app/jobs/__init__.py`
3. Register in scheduler setup (main.py)
4. Add corresponding cron environment variable
5. Test via `/api/v1/run-now?job=new_job_name`

### UI Section Development
1. Add section HTML structure to `index.html` within appropriate navigation group
2. Implement `loadSectionData()` function with API calls
3. Create `displaySectionData()` for rendering with consistent header pattern
4. Add navigation link with appropriate emoji icon and Russian text
5. Update `updatePageHeader()` titles mapping
6. Handle loading states and error cases with refresh button pattern

### Database Schema Changes
1. Create Alembic migration: `alembic revision -m "description"`
2. Define upgrade/downgrade operations
3. Test locally then apply: `alembic upgrade head`
4. Update corresponding SQLAlchemy models if needed