# Repository Guidelines

## Project Structure & Module Organization
Code lives under `app/`: `core/` (config, DB, logger), `data/` (API clients, mappers), `domain/` (models, indices, text), and `jobs/` (APScheduler tasks). Database migrations are in `alembic/versions/`. Utilities (e.g., `scripts/backtest.py`, `scripts/run_pipeline.py`) support analysis or experimentation. Containers are defined at the repo root (`Dockerfile`, `docker-compose.yml`, `.env.example`). Add `__init__.py` files when creating new packages so modules stay importable.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`: create a local runtime identical to the container.
- `docker compose up --build -d`: build and start FastAPI + Postgres; verify via `curl http://localhost:8000/health`.
- `docker compose ps`: should show `app` (API) and `scheduler` (APScheduler runner).
- `docker compose exec -T app python -m compileall -q app`: fast syntax check after edits.
- `docker compose exec -T app alembic upgrade head`: apply DB migrations (schema source of truth is Alembic).
- Dry-run all jobs without cron:
  ```bash
  docker compose exec -T app python -c "import asyncio; from app.core.db import SessionLocal, init_db; from app.jobs import sync_data, compute_indices, build_predictions, evaluate_results; async def main():\n    await init_db()\n    async with SessionLocal() as s:\n      await sync_data.run(s)\n      await compute_indices.run(s)\n      await build_predictions.run(s)\n      await evaluate_results.run(s)\n  asyncio.run(main())"
  ```
- `docker compose down` stops everything; `docker compose restart app` after `.env` updates.
- Note: this repo's docker image does not bind-mount the source; after code changes run `docker compose up --build -d app`.
- Maintenance: `docker compose exec -T app python -c "import asyncio; from app.core.db import SessionLocal, init_db; from app.jobs import maintenance; async def main():\n  await init_db();\n  async with SessionLocal() as s:\n    await maintenance.run(s)\n  asyncio.run(main())"`

## Coding Style & Naming Conventions
Use Python 3.12, 4-space indentation, `snake_case` names, and PascalCase classes. Keep imports absolute. Write concise comments only when business logic is non-obvious. Maintain type hints for async SQLAlchemy helpers and Pydantic models. Probability flows: Poisson/Logistic/Dixon-Coles/Hybrid (controlled by USE_* flags), Decimal for EV/ROI/Brier/LogLoss.

## Testing Guidelines
Validate changes with `python -m compileall` and the dry-run job sequence. Tests live under `tests/` (Poisson/Dixon-Coles/Hybrid, API sanity); run via `pytest -q` in venv. Document analytical checks (rolling percentiles, cache TTLs, Brier/LogLoss) in PRs with sample output.

## Quality Report & Shadow Filters
- Quality Report powers the UI quality cards; refresh via `GET /api/v1/quality_report?refresh=1` (admin token required).
- Shadow filters (what-if scenarios) are defined in `app/jobs/quality_report.py` under `SHADOW_FILTERS` and rendered in UI details.

## Snapshot Coverage Tuning
- Pre-kickoff snapshot coverage affects CLV reliability; tune `SNAPSHOT_AUTOFILL_*`, `ODDS_FRESHNESS_*`, `JOB_*_CRON`, and `SYNC_DATA_ODDS_LOOKAHEAD_HOURS` in `.env`.
- After changing `.env`, rebuild containers (image does not bind-mount source): `docker compose up --build -d app scheduler`.

## Security / Admin Endpoints
- `ADMIN_TOKEN` is required at process startup; UI and admin endpoints expect `X-Admin-Token: <token>`.

## Commit & Pull Request Guidelines
Commits should be small, imperative (“add historical window toggle”, “fix JSONB cache”). Note schema or env impacts in the body. Pull requests must describe the change, list verification steps (commands, SQL snippets, API responses), mention new env vars/migrations, and attach relevant logs or screenshots. Keep branches rebased so migrations apply cleanly.
