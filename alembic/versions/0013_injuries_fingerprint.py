"""Add injuries.fingerprint for dedup

Revision ID: 0013_injuries_fingerprint
Revises: 0012_injuries_league_baselines
Create Date: 2025-12-13
"""

from alembic import op


revision = "0013_injuries_fingerprint"
down_revision = "0012_injuries_league_baselines"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE injuries ADD COLUMN IF NOT EXISTS fingerprint TEXT")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_injuries_fingerprint
        ON injuries (fingerprint)
        WHERE fingerprint IS NOT NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_injuries_fingerprint")
    op.execute("ALTER TABLE injuries DROP COLUMN IF EXISTS fingerprint")

