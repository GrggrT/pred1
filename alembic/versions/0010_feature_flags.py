"""Add feature_flags to predictions

Revision ID: 0010_feature_flags
Revises: 0009_add_signal_score
Create Date: 2025-03-25 03:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_feature_flags"
down_revision = "0009_add_signal_score"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE predictions ADD COLUMN IF NOT EXISTS feature_flags JSONB DEFAULT '{}'::jsonb")


def downgrade():
    op.execute("ALTER TABLE predictions DROP COLUMN IF EXISTS feature_flags")
