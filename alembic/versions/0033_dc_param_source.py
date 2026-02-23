"""Add param_source column to DC tables for goals/xG dual-mode

Revision ID: 0033_dc_param_source
Revises: 0032_publication_idempotency
Create Date: 2026-02-22 12:00:00.000000
"""

revision = "0033_dc_param_source"
down_revision = "0032_publication_idempotency"

from alembic import op
import sqlalchemy as sa


def upgrade():
    # Add param_source column to team_strength_params
    op.add_column(
        "team_strength_params",
        sa.Column("param_source", sa.Text(), server_default="goals", nullable=False),
    )
    # Drop old unique constraint and create new one including param_source
    op.drop_constraint(
        "team_strength_params_team_id_league_id_season_as_of_date_key",
        "team_strength_params",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_tsp_lookup",
        "team_strength_params",
        ["team_id", "league_id", "season", "as_of_date", "param_source"],
    )

    # Add param_source column to dc_global_params
    op.add_column(
        "dc_global_params",
        sa.Column("param_source", sa.Text(), server_default="goals", nullable=False),
    )
    # Drop old unique constraint and create new one including param_source
    op.drop_constraint(
        "dc_global_params_league_id_season_as_of_date_key",
        "dc_global_params",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_dcgp_lookup",
        "dc_global_params",
        ["league_id", "season", "as_of_date", "param_source"],
    )


def downgrade():
    # dc_global_params: restore original constraint
    op.drop_constraint("uq_dcgp_lookup", "dc_global_params", type_="unique")
    # Delete xg rows before restoring original unique constraint
    op.execute("DELETE FROM dc_global_params WHERE param_source != 'goals'")
    op.create_unique_constraint(
        "dc_global_params_league_id_season_as_of_date_key",
        "dc_global_params",
        ["league_id", "season", "as_of_date"],
    )
    op.drop_column("dc_global_params", "param_source")

    # team_strength_params: restore original constraint
    op.drop_constraint("uq_tsp_lookup", "team_strength_params", type_="unique")
    op.execute("DELETE FROM team_strength_params WHERE param_source != 'goals'")
    op.create_unique_constraint(
        "team_strength_params_team_id_league_id_season_as_of_date_key",
        "team_strength_params",
        ["team_id", "league_id", "season", "as_of_date"],
    )
    op.drop_column("team_strength_params", "param_source")
