"""Add team Elo ratings table

Revision ID: 0007_team_elo_ratings
Revises: 0006_fix_schema_alignment
Create Date: 2025-03-25 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_team_elo_ratings"
down_revision = "0006_fix_schema_alignment"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS team_elo_ratings (
            team_id BIGINT PRIMARY KEY,
            rating NUMERIC(8, 3) NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS team_elo_ratings")
