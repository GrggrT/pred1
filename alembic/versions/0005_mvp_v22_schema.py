"""Align schema to MVP v2.2

Revision ID: 0005_mvp_v22_schema
Revises: 0004_predictions_pk_id
Create Date: 2025-03-20 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_mvp_v22_schema"
down_revision = "0004_predictions_pk_id"
branch_labels = None
depends_on = None


def upgrade():
    # fixtures
    op.alter_column("fixtures", "kickoff", type_=sa.DateTime(timezone=True))
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS home_xg NUMERIC(5,2)")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS away_xg NUMERIC(5,2)")
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS has_odds BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute(
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS stats_downloaded BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_league_season ON fixtures(league_id, season)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_home_kickoff ON fixtures(home_team_id, kickoff)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_fixtures_away_kickoff ON fixtures(away_team_id, kickoff)")

    # odds
    op.alter_column("odds", "fetched_at", type_=sa.DateTime(timezone=True))
    op.alter_column("odds", "home_win", type_=sa.Numeric(8, 3))
    op.alter_column("odds", "draw", type_=sa.Numeric(8, 3))
    op.alter_column("odds", "away_win", type_=sa.Numeric(8, 3))

    # match_indices
    for col in [
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
    ]:
        op.execute(f"ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS {col} NUMERIC(8,3)")
    op.execute("ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS home_rest_hours INTEGER")
    op.execute("ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS away_rest_hours INTEGER")
    op.execute(
        "ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.alter_column("match_indices", "created_at", type_=sa.DateTime(timezone=True))

    # predictions
    op.alter_column("predictions", "confidence", type_=sa.Numeric(6, 4))
    op.alter_column("predictions", "initial_odd", type_=sa.Numeric(8, 3))
    op.alter_column("predictions", "value_index", type_=sa.Numeric(8, 4))
    op.alter_column("predictions", "profit", type_=sa.Numeric(10, 3))
    op.execute(
        "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS selection_code VARCHAR(20) NOT NULL DEFAULT 'SKIP'"
    )
    op.execute(
        "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'PENDING'"
    )
    op.execute(
        "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_predictions_status_created ON predictions(status, created_at)"
    )
    op.alter_column("predictions", "created_at", type_=sa.DateTime(timezone=True))


def downgrade():
    # Downgrade is intentionally minimal for MVP; columns remain.
    pass
