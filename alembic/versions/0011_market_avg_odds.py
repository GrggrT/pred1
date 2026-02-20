"""Add market average odds columns

Revision ID: 0011_market_avg_odds
Revises: 0010_feature_flags
Create Date: 2025-03-25 04:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_market_avg_odds"
down_revision = "0010_feature_flags"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE odds ADD COLUMN IF NOT EXISTS market_avg_home_win NUMERIC(8,3)")
    op.execute("ALTER TABLE odds ADD COLUMN IF NOT EXISTS market_avg_draw NUMERIC(8,3)")
    op.execute("ALTER TABLE odds ADD COLUMN IF NOT EXISTS market_avg_away_win NUMERIC(8,3)")
    op.execute("ALTER TABLE odds ADD COLUMN IF NOT EXISTS market_avg_over_2_5 NUMERIC(8,3)")
    op.execute("ALTER TABLE odds ADD COLUMN IF NOT EXISTS market_avg_under_2_5 NUMERIC(8,3)")


def downgrade():
    op.execute("ALTER TABLE odds DROP COLUMN IF EXISTS market_avg_home_win")
    op.execute("ALTER TABLE odds DROP COLUMN IF EXISTS market_avg_draw")
    op.execute("ALTER TABLE odds DROP COLUMN IF EXISTS market_avg_away_win")
    op.execute("ALTER TABLE odds DROP COLUMN IF EXISTS market_avg_over_2_5")
    op.execute("ALTER TABLE odds DROP COLUMN IF EXISTS market_avg_under_2_5")
