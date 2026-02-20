"""Persist job run history

Revision ID: 0016_job_runs
Revises: 0015_history_indexes
Create Date: 2025-12-13
"""

from alembic import op


revision = "0016_job_runs"
down_revision = "0015_history_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS job_runs (
          id BIGSERIAL PRIMARY KEY,
          job_name TEXT NOT NULL,
          status VARCHAR(20) NOT NULL DEFAULT 'running',
          triggered_by TEXT,
          started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          finished_at TIMESTAMPTZ,
          error TEXT,
          meta JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_job_started ON job_runs(job_name, started_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_status_started ON job_runs(status, started_at DESC)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_job_runs_status_started")
    op.execute("DROP INDEX IF EXISTS idx_job_runs_job_started")
    op.execute("DROP TABLE IF EXISTS job_runs")

