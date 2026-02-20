"""Add settled_at to predictions

Revision ID: 0023_predictions_settled_at
Revises: 0022_retention_indexes
Create Date: 2025-12-14
"""

from alembic import op


revision = "0023_predictions_settled_at"
down_revision = "0022_retention_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ")
    op.execute("CREATE INDEX IF NOT EXISTS idx_predictions_settled_at ON predictions(settled_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_predictions_status_settled_at ON predictions(status, settled_at DESC)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_predictions_status_settled_at")
    op.execute("DROP INDEX IF EXISTS idx_predictions_settled_at")
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS settled_at")
