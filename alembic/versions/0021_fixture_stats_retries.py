"""Add fixture stats retry fields

Revision ID: 0021_fixture_stats_retries
Revises: 0020_team_standings
Create Date: 2025-12-13 00:00:00.000000
"""

from alembic import op


revision = "0021_fixture_stats_retries"
down_revision = "0020_team_standings"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS stats_attempted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS stats_attempts INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS stats_gave_up BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS stats_error TEXT")

    # Backfill attempts for existing rows.
    op.execute("UPDATE fixtures SET stats_attempts = COALESCE(stats_attempts, 0) WHERE stats_attempts IS NULL")

    # If previous versions marked stats_downloaded=true without xG, allow retries.
    op.execute(
        """
        UPDATE fixtures
        SET stats_downloaded = FALSE,
            stats_gave_up = FALSE,
            stats_error = NULL
        WHERE stats_downloaded = TRUE
          AND (home_xg IS NULL OR away_xg IS NULL)
          AND status IN ('FT','AET','PEN')
        """
    )


def downgrade():
    op.execute("ALTER TABLE fixtures DROP COLUMN IF EXISTS stats_error")
    op.execute("ALTER TABLE fixtures DROP COLUMN IF EXISTS stats_gave_up")
    op.execute("ALTER TABLE fixtures DROP COLUMN IF EXISTS stats_attempts")
    op.execute("ALTER TABLE fixtures DROP COLUMN IF EXISTS stats_attempted_at")

