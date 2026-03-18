"""Add slug column to leagues table for SEO-friendly URLs.

Revision ID: 0039_league_slugs
Revises: 0038_ai_office_tables
Create Date: 2026-03-16 22:00:00.000000
"""

revision = "0039_league_slugs"
down_revision = "0038_ai_office_tables"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # 1. Add nullable slug column
    op.add_column("leagues", sa.Column("slug", sa.String(100), nullable=True))

    # 2. Populate slugs from league names:
    #    "Premier League" → "premier-league"
    #    "La Liga" → "la-liga"
    op.execute("""
        UPDATE leagues
        SET slug = LOWER(
            REGEXP_REPLACE(
                REGEXP_REPLACE(
                    TRIM(name),
                    '[^a-zA-Z0-9\\s-]', '', 'g'
                ),
                '\\s+', '-', 'g'
            )
        )
        WHERE slug IS NULL AND name IS NOT NULL
    """)

    # 3. Handle any remaining NULLs (fallback to id-based slug)
    op.execute("""
        UPDATE leagues SET slug = 'league-' || id WHERE slug IS NULL OR slug = ''
    """)

    # 4. Make NOT NULL and UNIQUE
    op.alter_column("leagues", "slug", nullable=False)
    op.create_unique_constraint("uq_leagues_slug", "leagues", ["slug"])
    op.create_index("idx_leagues_slug", "leagues", ["slug"])


def downgrade() -> None:
    op.drop_index("idx_leagues_slug", table_name="leagues")
    op.drop_constraint("uq_leagues_slug", "leagues", type_="unique")
    op.drop_column("leagues", "slug")
