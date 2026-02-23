"""Add fixture status elapsed minute

Revision ID: 0031_fixture_status_elapsed
Revises: 0030_dixon_coles_params
Create Date: 2026-02-21 00:00:00.000000
"""
from alembic import op


revision = "0031_fixture_status_elapsed"
down_revision = "0030_dixon_coles_params"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE fixtures ADD COLUMN IF NOT EXISTS status_elapsed INTEGER")


def downgrade():
    op.execute("ALTER TABLE fixtures DROP COLUMN IF EXISTS status_elapsed")
