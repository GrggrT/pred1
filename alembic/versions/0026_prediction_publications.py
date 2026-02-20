"""add prediction publications log

Revision ID: 0026_prediction_publications
Revises: 0025_elo_league_params
Create Date: 2025-12-26 13:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0026_prediction_publications"
down_revision = "0025_elo_league_params"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "prediction_publications",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("fixture_id", sa.BigInteger(), nullable=False),
        sa.Column("market", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("experimental", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("headline_message_id", sa.BigInteger(), nullable=True),
        sa.Column("analysis_message_id", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_prediction_publications_fixture_market",
        "prediction_publications",
        ["fixture_id", "market"],
    )
    op.create_index(
        "ix_prediction_publications_lang_status",
        "prediction_publications",
        ["language", "status"],
    )
    op.create_index(
        "ix_prediction_publications_created_at",
        "prediction_publications",
        ["created_at"],
    )


def downgrade():
    op.drop_index("ix_prediction_publications_created_at", table_name="prediction_publications")
    op.drop_index("ix_prediction_publications_lang_status", table_name="prediction_publications")
    op.drop_index("ix_prediction_publications_fixture_market", table_name="prediction_publications")
    op.drop_table("prediction_publications")
