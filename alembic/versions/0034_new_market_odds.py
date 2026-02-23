"""Add odds columns for new markets: O/U 1.5, O/U 3.5, BTTS, Double Chance

Revision ID: 0034_new_market_odds
Revises: 0033_dc_param_source
Create Date: 2026-02-22 18:00:00.000000
"""

revision = "0034_new_market_odds"
down_revision = "0033_dc_param_source"

from alembic import op
import sqlalchemy as sa


_COLUMNS = [
    # O/U 1.5
    "over_1_5", "under_1_5",
    # O/U 3.5
    "over_3_5", "under_3_5",
    # BTTS
    "btts_yes", "btts_no",
    # Double Chance
    "dc_1x", "dc_x2", "dc_12",
]

_TABLES = ["odds", "odds_snapshots"]


def upgrade():
    for table in _TABLES:
        for col in _COLUMNS:
            op.add_column(
                table,
                sa.Column(col, sa.Numeric(8, 3), nullable=True),
            )


def downgrade():
    for table in _TABLES:
        for col in reversed(_COLUMNS):
            op.drop_column(table, col)
