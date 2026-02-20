"""Add indexes for history queries

Revision ID: 0015_history_indexes
Revises: 0014_totals_settlement
Create Date: 2025-12-13
"""

from alembic import op


revision = "0015_history_indexes"
down_revision = "0014_totals_settlement"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_kickoff ON fixtures(kickoff)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_league_kickoff ON fixtures(league_id, kickoff)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_predictions_created_at ON predictions(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_predictions_status_created_at ON predictions(status, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_predictions_totals_created_at ON predictions_totals(created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_predictions_totals_status_created_at ON predictions_totals(status, created_at)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_predictions_totals_status_created_at")
    op.execute("DROP INDEX IF EXISTS idx_predictions_totals_created_at")
    op.execute("DROP INDEX IF EXISTS idx_predictions_status_created_at")
    op.execute("DROP INDEX IF EXISTS idx_predictions_created_at")
    op.execute("DROP INDEX IF EXISTS idx_fixtures_league_kickoff")
    op.execute("DROP INDEX IF EXISTS idx_fixtures_kickoff")

