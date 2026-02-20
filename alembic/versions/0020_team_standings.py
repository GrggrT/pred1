"""Add team_standings table

Revision ID: 0020_team_standings
Revises: 0019_prediction_decisions
Create Date: 2025-12-13 00:00:00.000000
"""

from alembic import op


revision = "0020_team_standings"
down_revision = "0019_prediction_decisions"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS team_standings (
          team_id BIGINT NOT NULL,
          league_id INTEGER NOT NULL,
          season INTEGER NOT NULL,
          rank INTEGER NULL,
          points INTEGER NULL,
          played INTEGER NULL,
          goals_for INTEGER NULL,
          goals_against INTEGER NULL,
          goal_diff INTEGER NULL,
          form TEXT NULL,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (team_id, league_id, season)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_standings_league_season_rank ON team_standings(league_id, season, rank)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_standings_team ON team_standings(team_id, league_id, season)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_team_standings_team")
    op.execute("DROP INDEX IF EXISTS idx_team_standings_league_season_rank")
    op.execute("DROP TABLE IF EXISTS team_standings")

