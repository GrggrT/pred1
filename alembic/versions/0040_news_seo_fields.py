"""Add SEO fields to news_articles: meta_description, tags, reading_time, word_count, author, image_url.

Revision ID: 0040_news_seo_fields
Revises: 0039_league_slugs
Create Date: 2026-03-18 20:00:00.000000
"""

revision = "0040_news_seo_fields"
down_revision = "0039_league_slugs"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def upgrade() -> None:
    op.add_column("news_articles", sa.Column("meta_description", sa.String(300), nullable=True))
    op.add_column("news_articles", sa.Column("tags", JSONB, server_default=sa.text("'[]'::jsonb"), nullable=True))
    op.add_column("news_articles", sa.Column("reading_time", sa.SmallInteger, nullable=True))
    op.add_column("news_articles", sa.Column("word_count", sa.Integer, nullable=True))
    op.add_column("news_articles", sa.Column("author", sa.String(100), server_default=sa.text("'FVB AI Analytics'"), nullable=True))
    op.add_column("news_articles", sa.Column("image_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("news_articles", "image_url")
    op.drop_column("news_articles", "author")
    op.drop_column("news_articles", "word_count")
    op.drop_column("news_articles", "reading_time")
    op.drop_column("news_articles", "tags")
    op.drop_column("news_articles", "meta_description")
