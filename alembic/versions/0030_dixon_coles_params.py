"""Add Dixon-Coles parameter tables

Revision ID: 0030_dixon_coles_params
Revises: 0029_match_indices_xg
Create Date: 2026-02-21 00:00:00.000000
"""
from alembic import op


revision = "0030_dixon_coles_params"
down_revision = "0029_match_indices_xg"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS team_strength_params (
            id SERIAL PRIMARY KEY,
            team_id INTEGER NOT NULL,
            league_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            as_of_date DATE NOT NULL,
            attack NUMERIC(10,6) NOT NULL,
            defense NUMERIC(10,6) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE(team_id, league_id, season, as_of_date)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_tsp_lookup ON team_strength_params(league_id, season, as_of_date)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_tsp_team ON team_strength_params(team_id, as_of_date)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS dc_global_params (
            id SERIAL PRIMARY KEY,
            league_id INTEGER NOT NULL,
            season INTEGER NOT NULL,
            as_of_date DATE NOT NULL,
            home_advantage NUMERIC(10,6) NOT NULL,
            rho NUMERIC(10,6) NOT NULL,
            xi NUMERIC(10,6) NOT NULL,
            log_likelihood NUMERIC(14,4),
            n_matches INTEGER,
            n_teams INTEGER,
            fit_seconds NUMERIC(8,2),
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE(league_id, season, as_of_date)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_dcgp_lookup ON dc_global_params(league_id, season, as_of_date)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS dc_global_params")
    op.execute("DROP TABLE IF EXISTS team_strength_params")
