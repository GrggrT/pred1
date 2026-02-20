"""Add retention-friendly indexes

Revision ID: 0022_retention_indexes
Revises: 0021_fixture_stats_retries
Create Date: 2025-12-13 00:00:00.000000
"""

from alembic import op


revision = "0022_retention_indexes"
down_revision = "0021_fixture_stats_retries"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS idx_injuries_created_at ON injuries(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_finished_at ON job_runs(finished_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_odds_snapshots_fetched_at ON odds_snapshots(fetched_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_stats_attempted_at ON fixtures(stats_attempted_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_api_cache_expires ON api_cache(expires_at)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_api_cache_expires")
    op.execute("DROP INDEX IF EXISTS idx_fixtures_stats_attempted_at")
    op.execute("DROP INDEX IF EXISTS idx_odds_snapshots_fetched_at")
    op.execute("DROP INDEX IF EXISTS idx_job_runs_finished_at")
    op.execute("DROP INDEX IF EXISTS idx_injuries_created_at")

