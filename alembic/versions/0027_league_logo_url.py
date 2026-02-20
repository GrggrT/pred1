"""add league logo url

Revision ID: 0027_league_logo_url
Revises: 0026_prediction_publications
Create Date: 2025-12-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0027_league_logo_url"
down_revision = "0026_prediction_publications"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("leagues", sa.Column("logo_url", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("leagues", "logo_url")
