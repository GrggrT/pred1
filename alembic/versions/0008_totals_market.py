"""Add totals odds columns and predictions_totals table

Revision ID: 0008_totals_market
Revises: 0007_team_elo_ratings
Create Date: 2025-03-25 01:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_totals_market"
down_revision = "0007_team_elo_ratings"
branch_labels = None
depends_on = None


def upgrade():
    # odds over/under 2.5
    op.execute("ALTER TABLE odds ADD COLUMN IF NOT EXISTS over_2_5 NUMERIC(8,3)")
    op.execute("ALTER TABLE odds ADD COLUMN IF NOT EXISTS under_2_5 NUMERIC(8,3)")
    # predictions_totals
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions_totals (
            fixture_id BIGINT REFERENCES fixtures(id),
            market VARCHAR(20) DEFAULT 'TOTAL',
            selection VARCHAR(20),
            confidence NUMERIC(6,4),
            initial_odd NUMERIC(8,3),
            value_index NUMERIC(8,4),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (fixture_id, market)
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS predictions_totals")
    op.execute("ALTER TABLE odds DROP COLUMN IF EXISTS over_2_5")
    op.execute("ALTER TABLE odds DROP COLUMN IF EXISTS under_2_5")
