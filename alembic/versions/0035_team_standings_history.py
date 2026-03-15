"""Add team_standings_history table for per-matchday standings

Stores reconstructed league standings as of each match date,
enabling standings features in backtest mode without data leakage.

Revision ID: 0035_team_standings_history
Revises: 0034_new_market_odds
Create Date: 2026-03-13 00:00:00.000000
"""

revision = "0035_team_standings_history"
down_revision = "0034_new_market_odds"
branch_labels = None
depends_on = None

from alembic import op


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS team_standings_history (
          team_id       BIGINT   NOT NULL,
          league_id     INTEGER  NOT NULL,
          season        INTEGER  NOT NULL,
          as_of_date    DATE     NOT NULL,
          played        INTEGER  NOT NULL DEFAULT 0,
          won           INTEGER  NOT NULL DEFAULT 0,
          drawn         INTEGER  NOT NULL DEFAULT 0,
          lost          INTEGER  NOT NULL DEFAULT 0,
          goals_for     INTEGER  NOT NULL DEFAULT 0,
          goals_against INTEGER  NOT NULL DEFAULT 0,
          goal_diff     INTEGER  NOT NULL DEFAULT 0,
          points        INTEGER  NOT NULL DEFAULT 0,
          rank          INTEGER  NULL,
          ppg           NUMERIC(5,3) NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (team_id, league_id, season, as_of_date)
        )
        """
    )

    # Fast lookups for build_predictions LATERAL join
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tsh_lookup
        ON team_standings_history(team_id, league_id, season, as_of_date DESC)
        """
    )

    # For computing ranks within league/season/date
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tsh_league_date
        ON team_standings_history(league_id, season, as_of_date, points DESC, goal_diff DESC)
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_tsh_league_date")
    op.execute("DROP INDEX IF EXISTS idx_tsh_lookup")
    op.execute("DROP TABLE IF EXISTS team_standings_history")
