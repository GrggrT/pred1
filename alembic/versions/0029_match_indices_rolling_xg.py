"""Add rolling xG columns to match_indices

Revision ID: 0029_match_indices_xg
Revises: 0028_model_params
Create Date: 2026-02-20 00:00:00.000000
"""
from alembic import op


revision = "0029_match_indices_xg"
down_revision = "0028_model_params"
branch_labels = None
depends_on = None


def upgrade():
    for col in [
        "home_xg_l5", "home_xg_l10",
        "away_xg_l5", "away_xg_l10",
    ]:
        op.execute(f"ALTER TABLE match_indices ADD COLUMN IF NOT EXISTS {col} NUMERIC(8,3)")


def downgrade():
    for col in [
        "home_xg_l5", "home_xg_l10",
        "away_xg_l5", "away_xg_l10",
    ]:
        op.execute(f"ALTER TABLE match_indices DROP COLUMN IF EXISTS {col}")
