"""Add CMP-DC parameters storage: nu0, nu1, team_ha in dc_global_params and team_strength_params

Revision ID: 0037_cmp_dc_params
Revises: 0036_odds_opening_columns
Create Date: 2026-03-13 02:00:00.000000
"""

revision = "0037_cmp_dc_params"
down_revision = "0036_odds_opening_columns"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    # Global CMP-DC params: nu0, nu1
    op.add_column("dc_global_params", sa.Column("nu0", sa.Numeric(10, 6), nullable=True))
    op.add_column("dc_global_params", sa.Column("nu1", sa.Numeric(10, 6), nullable=True))

    # Team-specific home advantage deviation
    op.add_column("team_strength_params", sa.Column("home_advantage_delta", sa.Numeric(10, 6), nullable=True))


def downgrade():
    op.drop_column("team_strength_params", "home_advantage_delta")
    op.drop_column("dc_global_params", "nu1")
    op.drop_column("dc_global_params", "nu0")
