#!/usr/bin/env bash
set -euo pipefail

DB_NAME="${1:-fc_mvp_audit}"

if ! [[ "$DB_NAME" =~ ^[a-zA-Z0-9_]+$ ]]; then
  echo "Invalid DB name: $DB_NAME" >&2
  exit 1
fi

echo "[bootstrap] starting db container"
docker compose up -d db

echo "[bootstrap] recreating database: $DB_NAME"
docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $DB_NAME;"
docker compose exec -T db psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DB_NAME;"

echo "[bootstrap] running alembic upgrade head"
docker compose run --rm \
  -e "DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/${DB_NAME}" \
  app alembic upgrade head

echo "[bootstrap] checking tables"
docker compose exec -T db psql -U postgres -d "$DB_NAME" -c "\\dt"

required_tables=(teams fixtures predictions match_indices api_cache)
for table in "${required_tables[@]}"; do
  exists=$(docker compose exec -T db psql -U postgres -d "$DB_NAME" -tAc "SELECT to_regclass('public.${table}')")
  if [[ -z "$exists" || "$exists" == "null" ]]; then
    echo "[bootstrap] missing table: $table" >&2
    exit 1
  fi
done

echo "[bootstrap] success"
