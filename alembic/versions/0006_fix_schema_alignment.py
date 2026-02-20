"""Fix schema alignment for MVP v2.2

Revision ID: 0006_fix_schema_alignment
Revises: 0005_mvp_v22_schema
Create Date: 2025-03-20 01:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_fix_schema_alignment"
down_revision = "0005_mvp_v22_schema"
branch_labels = None
depends_on = None


def upgrade():
    # fixtures: add missing columns if absent and indexes
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS home_xg NUMERIC(5,2)")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS away_xg NUMERIC(5,2)")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS has_odds BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS stats_downloaded BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_league_season ON fixtures(league_id, season)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_home_kickoff ON fixtures(home_team_id, kickoff)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_away_kickoff ON fixtures(away_team_id, kickoff)")

    # match_indices: align types and add fields
    numeric_cols = [
        "fatigue_home",
        "fatigue_away",
        "fatigue_diff",
        "chaos_match",
        "chaos_home",
        "chaos_away",
        "home_form_points",
        "away_form_points",
        "home_goals_scored_avg",
        "home_goals_conceded_avg",
        "away_goals_scored_avg",
        "away_goals_conceded_avg",
    ]
    for col in numeric_cols:
        op.execute(f"ALTER TABLE match_indices ALTER COLUMN {col} TYPE NUMERIC(8,3) USING {col}::numeric")
    new_cols = [
        "home_form_for",
        "home_form_against",
        "away_form_for",
        "away_form_against",
        "home_class_for",
        "home_class_against",
        "away_class_for",
        "away_class_against",
        "home_venue_for",
        "home_venue_against",
        "away_venue_for",
        "away_venue_against",
    ]
    for col in new_cols:
        op.execute(f"ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS {col} NUMERIC(8,3)")
    op.execute("ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS home_rest_hours INTEGER")
    op.execute("ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS away_rest_hours INTEGER")
    op.execute("ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()")

    # predictions: align numeric types and defaults
    op.execute("ALTER TABLE predictions ALTER COLUMN confidence TYPE NUMERIC(6,4) USING confidence::numeric")
    op.execute("ALTER TABLE predictions ALTER COLUMN initial_odd TYPE NUMERIC(8,3) USING initial_odd::numeric")
    op.execute("ALTER TABLE predictions ALTER COLUMN value_index TYPE NUMERIC(8,4) USING value_index::numeric")
    op.execute("ALTER TABLE predictions ALTER COLUMN profit TYPE NUMERIC(10,3) USING profit::numeric")
    op.execute("ALTER TABLE predictions ALTER COLUMN created_at TYPE TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS selection_code VARCHAR(20) NOT NULL DEFAULT 'SKIP'"
    )
    op.execute(
        "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'PENDING'"
    )
    op.execute("ALTER TABLE predictions ALTER COLUMN selection_code SET NOT NULL")
    op.execute("ALTER TABLE predictions ALTER COLUMN selection_code SET DEFAULT 'SKIP'")
    op.execute("ALTER TABLE predictions ALTER COLUMN status SET NOT NULL")
    op.execute("ALTER TABLE predictions ALTER COLUMN status SET DEFAULT 'PENDING'")
    op.execute("CREATE INDEX IF NOT EXISTS idx_predictions_status_created ON predictions(status, created_at)")


def downgrade():
    pass
