"""Add odds_snapshots table for true backtest

Revision ID: 0018_odds_snapshots
Revises: 0017_history_sort_expr_indexes
Create Date: 2025-12-13
"""

from alembic import op


revision = "0018_odds_snapshots"
down_revision = "0017_history_sort_expr_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS odds_snapshots (
          fixture_id BIGINT NOT NULL REFERENCES fixtures(id),
          bookmaker_id INTEGER NOT NULL,
          fetched_at TIMESTAMPTZ NOT NULL,
          home_win NUMERIC(8,3),
          draw NUMERIC(8,3),
          away_win NUMERIC(8,3),
          over_2_5 NUMERIC(8,3),
          under_2_5 NUMERIC(8,3),
          market_avg_home_win NUMERIC(8,3),
          market_avg_draw NUMERIC(8,3),
          market_avg_away_win NUMERIC(8,3),
          market_avg_over_2_5 NUMERIC(8,3),
          market_avg_under_2_5 NUMERIC(8,3),
          PRIMARY KEY (fixture_id, bookmaker_id, fetched_at)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_odds_snapshots_fixture_bookmaker_fetched
        ON odds_snapshots (fixture_id, bookmaker_id, fetched_at DESC)
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_odds_snapshots_fixture_bookmaker_fetched")
    op.execute("DROP TABLE IF EXISTS odds_snapshots")
