"""init baseline schema

Revision ID: 0001
Revises:
Create Date: 2023-01-01 00:00:00.000000
"""
from alembic import op


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS teams (
          id BIGINT PRIMARY KEY,
          name VARCHAR(100),
          league_id INTEGER
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fixtures (
          id BIGINT PRIMARY KEY,
          league_id INTEGER,
          season INTEGER,
          kickoff TIMESTAMP WITHOUT TIME ZONE NOT NULL,
          home_team_id BIGINT,
          away_team_id BIGINT,
          status VARCHAR(20),
          home_goals INTEGER,
          away_goals INTEGER
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS match_indices (
          fixture_id BIGINT PRIMARY KEY,
          fatigue_home DOUBLE PRECISION,
          fatigue_away DOUBLE PRECISION,
          fatigue_diff DOUBLE PRECISION,
          chaos_match DOUBLE PRECISION,
          chaos_home DOUBLE PRECISION,
          chaos_away DOUBLE PRECISION,
          updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS predictions (
          fixture_id BIGINT PRIMARY KEY,
          confidence DOUBLE PRECISION,
          created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS api_cache (
          cache_key TEXT PRIMARY KEY,
          payload JSONB NOT NULL DEFAULT '{}'::jsonb,
          expires_at TIMESTAMPTZ NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS api_cache")
    op.execute("DROP TABLE IF EXISTS predictions")
    op.execute("DROP TABLE IF EXISTS match_indices")
    op.execute("DROP TABLE IF EXISTS fixtures")
    op.execute("DROP TABLE IF EXISTS teams")
