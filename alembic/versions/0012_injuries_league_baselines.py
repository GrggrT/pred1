"""Add injuries and league_baselines tables

Revision ID: 0012_injuries_league_baselines
Revises: 0011_market_avg_odds
Create Date: 2025-03-25 05:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_injuries_league_baselines"
down_revision = "0011_market_avg_odds"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS injuries (
            id SERIAL PRIMARY KEY,
            player_name TEXT,
            team_id BIGINT,
            league_id INTEGER,
            fixture_id BIGINT,
            reason TEXT,
            type TEXT,
            status TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS league_baselines (
            league_id INTEGER,
            season INTEGER,
            date_key DATE,
            avg_home_xg NUMERIC(5,3),
            avg_away_xg NUMERIC(5,3),
            draw_freq NUMERIC(5,4),
            avg_goals NUMERIC(5,3),
            PRIMARY KEY (league_id, season, date_key)
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS league_baselines")
    op.execute("DROP TABLE IF EXISTS injuries")
