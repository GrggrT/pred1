"""Add idempotency key for prediction publications

Revision ID: 0032_publication_idempotency
Revises: 0031_fixture_status_elapsed
Create Date: 2026-02-21 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0032_publication_idempotency"
down_revision = "0031_fixture_status_elapsed"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "prediction_publications",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_prediction_publications_idempotency_key",
        "prediction_publications",
        ["idempotency_key"],
        unique=False,
    )
    op.create_index(
        "uq_prediction_publications_published_idempotency",
        "prediction_publications",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL AND status IN ('ok', 'published')"),
    )


def downgrade():
    op.drop_index("uq_prediction_publications_published_idempotency", table_name="prediction_publications")
    op.drop_index("ix_prediction_publications_idempotency_key", table_name="prediction_publications")
    op.drop_column("prediction_publications", "idempotency_key")
