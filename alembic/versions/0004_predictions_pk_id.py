"""add predictions id pk

Revision ID: 0004_predictions_pk_id
Revises: 0003_mvp_schema
Create Date: 2025-02-18 00:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_predictions_pk_id"
down_revision = "0003_mvp_schema"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("predictions", sa.Column("id", sa.Integer(), nullable=True))
    op.execute(sa.text("CREATE SEQUENCE IF NOT EXISTS predictions_id_seq"))
    op.execute(
        sa.text(
            "SELECT setval('predictions_id_seq', COALESCE((SELECT MAX(id) FROM predictions), 1), false)"
        )
    )
    op.execute(sa.text("UPDATE predictions SET id = nextval('predictions_id_seq') WHERE id IS NULL"))

    op.drop_constraint("predictions_pkey", "predictions", type_="primary")
    op.create_primary_key("predictions_pkey", "predictions", ["id"])
    op.create_unique_constraint("uq_predictions_fixture_id", "predictions", ["fixture_id"])

    op.execute(sa.text("ALTER TABLE predictions ALTER COLUMN id SET DEFAULT nextval('predictions_id_seq')"))
    op.alter_column("predictions", "id", nullable=False)


def downgrade():
    op.drop_constraint("uq_predictions_fixture_id", "predictions", type_="unique")
    op.drop_constraint("predictions_pkey", "predictions", type_="primary")
    op.create_primary_key("predictions_pkey", "predictions", ["fixture_id"])
    op.drop_column("predictions", "id")
    op.execute(sa.text("DROP SEQUENCE IF EXISTS predictions_id_seq"))
