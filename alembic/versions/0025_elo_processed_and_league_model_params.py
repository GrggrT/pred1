"""Add Elo processing markers + league params

Revision ID: 0025_elo_league_params
Revises: 0024_backfill_settled_at
Create Date: 2025-12-21 00:00:00.000000
"""

from alembic import op


revision = "0025_elo_league_params"
down_revision = "0024_backfill_settled_at"
branch_labels = None
depends_on = None


def upgrade():
    # Track whether a finished fixture has been applied to Elo ratings (idempotent replay).
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS elo_processed BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS elo_processed_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_fixtures_elo_processed_league_kickoff ON fixtures(league_id, elo_processed, kickoff)"
    )

    # Persist per-league/season params used by the model.
    op.execute("ALTER TABLE league_baselines ADD COLUMN IF NOT EXISTS dc_rho NUMERIC(6,4) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE league_baselines ADD COLUMN IF NOT EXISTS calib_alpha NUMERIC(6,4) NOT NULL DEFAULT 1")


def downgrade():
    # Best-effort downgrade; keep idempotent for environments that may already have the columns.
    op.execute("DROP INDEX IF EXISTS idx_fixtures_elo_processed_league_kickoff")
    op.execute("ALTER TABLE league_baselines DROP COLUMN IF EXISTS calib_alpha")
    op.execute("ALTER TABLE league_baselines DROP COLUMN IF EXISTS dc_rho")
    op.execute("ALTER TABLE fixtures DROP COLUMN IF EXISTS elo_processed_at")
    op.execute("ALTER TABLE fixtures DROP COLUMN IF EXISTS elo_processed")
