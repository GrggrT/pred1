"""mvp schema additions

Revision ID: 0003_mvp_schema
Revises: 0002_team_fatigue_raw
Create Date: 2025-02-18 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_mvp_schema"
down_revision = "0002_team_fatigue_raw"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "leagues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=50), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("TRUE"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column("teams", sa.Column("code", sa.String(length=10), nullable=True))
    op.add_column("teams", sa.Column("logo_url", sa.String(length=255), nullable=True))

    op.create_table(
        "odds",
        sa.Column("fixture_id", sa.BigInteger(), nullable=False),
        sa.Column("bookmaker_id", sa.Integer(), nullable=False),
        sa.Column("home_win", sa.Numeric(5, 2), nullable=True),
        sa.Column("draw", sa.Numeric(5, 2), nullable=True),
        sa.Column("away_win", sa.Numeric(5, 2), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("fixture_id", "bookmaker_id"),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"]),
    )

    op.add_column(
        "fixtures",
        sa.Column("processed_indices", sa.Boolean(), server_default=sa.text("FALSE"), nullable=True),
    )
    op.add_column(
        "fixtures",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.alter_column(
        "fixtures",
        "kickoff",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
    )

    op.add_column("match_indices", sa.Column("home_form_points", sa.Float(), nullable=True))
    op.add_column("match_indices", sa.Column("away_form_points", sa.Float(), nullable=True))
    op.add_column("match_indices", sa.Column("home_goals_scored_avg", sa.Float(), nullable=True))
    op.add_column("match_indices", sa.Column("home_goals_conceded_avg", sa.Float(), nullable=True))
    op.add_column("match_indices", sa.Column("away_goals_scored_avg", sa.Float(), nullable=True))
    op.add_column("match_indices", sa.Column("away_goals_conceded_avg", sa.Float(), nullable=True))
    op.add_column("match_indices", sa.Column("home_rest_days", sa.Float(), nullable=True))
    op.add_column("match_indices", sa.Column("away_rest_days", sa.Float(), nullable=True))
    op.alter_column(
        "match_indices",
        "updated_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        server_default=sa.text("now()"),
    )

    op.add_column("predictions", sa.Column("selection_code", sa.String(length=20), nullable=True))
    op.add_column("predictions", sa.Column("fair_odd", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("initial_odd", sa.Numeric(5, 2), nullable=True))
    op.add_column("predictions", sa.Column("value_index", sa.Float(), nullable=True))
    op.add_column("predictions", sa.Column("status", sa.String(length=20), server_default="PENDING", nullable=True))
    op.add_column("predictions", sa.Column("profit", sa.Numeric(6, 2), nullable=True))
    op.alter_column(
        "predictions",
        "created_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=True,
        server_default=sa.text("now()"),
    )


def downgrade():
    op.alter_column(
        "predictions",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.drop_column("predictions", "profit")
    op.drop_column("predictions", "status")
    op.drop_column("predictions", "value_index")
    op.drop_column("predictions", "initial_odd")
    op.drop_column("predictions", "fair_odd")
    op.drop_column("predictions", "selection_code")

    op.alter_column(
        "match_indices",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=True,
    )
    op.drop_column("match_indices", "away_rest_days")
    op.drop_column("match_indices", "home_rest_days")
    op.drop_column("match_indices", "away_goals_conceded_avg")
    op.drop_column("match_indices", "away_goals_scored_avg")
    op.drop_column("match_indices", "home_goals_conceded_avg")
    op.drop_column("match_indices", "home_goals_scored_avg")
    op.drop_column("match_indices", "away_form_points")
    op.drop_column("match_indices", "home_form_points")

    op.alter_column(
        "fixtures",
        "kickoff",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=False,
    )
    op.drop_column("fixtures", "updated_at")
    op.drop_column("fixtures", "processed_indices")

    op.drop_table("odds")
    op.drop_column("teams", "logo_url")
    op.drop_column("teams", "code")
    op.drop_table("leagues")
