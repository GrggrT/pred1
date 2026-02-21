"""Create model_params table for trained model coefficients

Revision ID: 0028_model_params
Revises: 0027_league_logo_url
Create Date: 2026-02-20 00:00:00.000000
"""
from alembic import op


revision = "0028_model_params"
down_revision = "0027_league_logo_url"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_params (
            id SERIAL PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT 'global',
            league_id INTEGER,
            param_name TEXT NOT NULL,
            param_value NUMERIC(12,6) NOT NULL,
            metadata JSONB,
            trained_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(scope, league_id, param_name)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_params_scope_league ON model_params(scope, league_id)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_model_params_scope_league")
    op.execute("DROP TABLE IF EXISTS model_params")
