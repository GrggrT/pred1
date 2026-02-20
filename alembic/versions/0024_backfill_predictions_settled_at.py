"""Backfill predictions.settled_at for existing rows

Revision ID: 0024_backfill_settled_at
Revises: 0023_predictions_settled_at
Create Date: 2025-12-14
"""

from alembic import op


revision = "0024_backfill_settled_at"
down_revision = "0023_predictions_settled_at"
branch_labels = None
depends_on = None


def upgrade():
    # For legacy rows settled before settled_at existed, use fixture kickoff as a stable proxy.
    op.execute(
        """
        UPDATE predictions p
        SET settled_at = f.kickoff
        FROM fixtures f
        WHERE f.id = p.fixture_id
          AND p.settled_at IS NULL
          AND p.status IN ('WIN','LOSS','VOID')
          AND f.kickoff IS NOT NULL
        """
    )


def downgrade():
    # Best-effort rollback: only clear values that look like we backfilled from kickoff.
    op.execute(
        """
        UPDATE predictions p
        SET settled_at = NULL
        FROM fixtures f
        WHERE f.id = p.fixture_id
          AND p.settled_at = f.kickoff
          AND p.status IN ('WIN','LOSS','VOID')
        """
    )
