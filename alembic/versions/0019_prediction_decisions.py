"""Add prediction_decisions table for candidates and skip reasons

Revision ID: 0019_prediction_decisions
Revises: 0018_odds_snapshots
Create Date: 2025-12-13
"""

from alembic import op


revision = "0019_prediction_decisions"
down_revision = "0018_odds_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS prediction_decisions (
          fixture_id BIGINT NOT NULL REFERENCES fixtures(id),
          market VARCHAR(20) NOT NULL,
          payload JSONB,
          created_at TIMESTAMPTZ DEFAULT now(),
          updated_at TIMESTAMPTZ DEFAULT now(),
          PRIMARY KEY (fixture_id, market)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prediction_decisions_updated_at
        ON prediction_decisions (updated_at DESC)
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_prediction_decisions_updated_at")
    op.execute("DROP TABLE IF EXISTS prediction_decisions")
