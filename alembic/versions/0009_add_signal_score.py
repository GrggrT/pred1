"""Add signal_score to predictions

Revision ID: 0009_add_signal_score
Revises: 0008_totals_market
Create Date: 2025-03-25 02:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_add_signal_score"
down_revision = "0008_totals_market"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS signal_score NUMERIC(6,3)")


def downgrade():
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS signal_score")
