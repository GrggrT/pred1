"""Add opening odds columns to odds table for movement tracking

Stores the earliest observed odds per fixture/bookmaker so we can compute
odds_movement = closing_implied_prob - opening_implied_prob.

Revision ID: 0036_odds_opening_columns
Revises: 0035_team_standings_history
Create Date: 2026-03-13 01:00:00.000000
"""

revision = "0036_odds_opening_columns"
down_revision = "0035_team_standings_history"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    # Opening 1X2 odds (first snapshot per fixture)
    for col in ("opening_home_win", "opening_draw", "opening_away_win"):
        op.add_column("odds", sa.Column(col, sa.Numeric(8, 3), nullable=True))

    # Timestamp when opening odds were first recorded
    op.add_column("odds", sa.Column("opening_fetched_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill opening odds from earliest snapshot per fixture
    op.execute(
        """
        UPDATE odds o
        SET
          opening_home_win = sub.home_win,
          opening_draw = sub.draw,
          opening_away_win = sub.away_win,
          opening_fetched_at = sub.fetched_at
        FROM (
          SELECT DISTINCT ON (os.fixture_id, os.bookmaker_id)
            os.fixture_id, os.bookmaker_id,
            os.home_win, os.draw, os.away_win,
            os.fetched_at
          FROM odds_snapshots os
          WHERE os.home_win IS NOT NULL
          ORDER BY os.fixture_id, os.bookmaker_id, os.fetched_at ASC
        ) sub
        WHERE o.fixture_id = sub.fixture_id
          AND o.bookmaker_id = sub.bookmaker_id
          AND o.opening_home_win IS NULL
        """
    )


def downgrade():
    for col in ("opening_fetched_at", "opening_away_win", "opening_draw", "opening_home_win"):
        op.drop_column("odds", col)
