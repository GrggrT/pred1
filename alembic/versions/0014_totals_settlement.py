"""Add settlement fields for predictions_totals

Revision ID: 0014_totals_settlement
Revises: 0013_injuries_fingerprint
Create Date: 2025-12-13
"""

from alembic import op


revision = "0014_totals_settlement"
down_revision = "0013_injuries_fingerprint"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE predictions_totals ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'PENDING'")
    op.execute("ALTER TABLE predictions_totals ADD COLUMN IF NOT EXISTS profit NUMERIC(10,3)")
    op.execute("ALTER TABLE predictions_totals ADD COLUMN IF NOT EXISTS settled_at TIMESTAMPTZ")
    op.execute("CREATE INDEX IF NOT EXISTS idx_predictions_totals_status ON predictions_totals(status)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_predictions_totals_status")
    op.execute("ALTER TABLE predictions_totals DROP COLUMN IF EXISTS settled_at")
    op.execute("ALTER TABLE predictions_totals DROP COLUMN IF EXISTS profit")
    op.execute("ALTER TABLE predictions_totals DROP COLUMN IF EXISTS status")

