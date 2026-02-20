"""Add expression indexes for history sorting

Revision ID: 0017_history_sort_expr_indexes
Revises: 0016_job_runs
Create Date: 2025-12-13
"""

from alembic import op


revision = "0017_history_sort_expr_indexes"
down_revision = "0016_job_runs"
branch_labels = None
depends_on = None


def upgrade():
    # 1X2 predictions: speed up ORDER BY ev/profit/signal for history & picks.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_predictions_ev_expr
        ON predictions (((confidence * initial_odd) - 1))
        WHERE selection_code != 'SKIP'
          AND confidence IS NOT NULL
          AND initial_odd IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_predictions_signal_score
        ON predictions (signal_score)
        WHERE selection_code != 'SKIP'
          AND signal_score IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_predictions_profit
        ON predictions (profit)
        WHERE selection_code != 'SKIP'
          AND profit IS NOT NULL
        """
    )

    # Totals predictions: speed up ORDER BY ev/profit.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_predictions_totals_ev_expr
        ON predictions_totals (((confidence * initial_odd) - 1))
        WHERE market = 'TOTAL'
          AND confidence IS NOT NULL
          AND initial_odd IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_predictions_totals_profit
        ON predictions_totals (profit)
        WHERE market = 'TOTAL'
          AND profit IS NOT NULL
        """
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_predictions_totals_profit")
    op.execute("DROP INDEX IF EXISTS idx_predictions_totals_ev_expr")
    op.execute("DROP INDEX IF EXISTS idx_predictions_profit")
    op.execute("DROP INDEX IF EXISTS idx_predictions_signal_score")
    op.execute("DROP INDEX IF EXISTS idx_predictions_ev_expr")

