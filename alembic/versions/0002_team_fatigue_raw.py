"""team fatigue history table

Revision ID: 0002_team_fatigue_raw
Revises: 0001
Create Date: 2025-02-17 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_team_fatigue_raw"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "team_fatigue_raw",
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("league_id", sa.BigInteger(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("fatigue_raw", sa.Numeric(), nullable=False),
        sa.PrimaryKeyConstraint("team_id", "as_of_date"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
    )
    op.create_index(
        "idx_team_fatigue_raw_league_date",
        "team_fatigue_raw",
        ["league_id", "as_of_date"],
    )


def downgrade():
    op.drop_index("idx_team_fatigue_raw_league_date", table_name="team_fatigue_raw")
    op.drop_table("team_fatigue_raw")
