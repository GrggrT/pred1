"""AI Office: 4 new tables — ai_office_reports, scout_reports, news_articles, news_sources

Revision ID: 0038_ai_office_tables
Revises: 0037_cmp_dc_params
Create Date: 2026-03-15 18:00:00.000000
"""

revision = "0038_ai_office_tables"
down_revision = "0037_cmp_dc_params"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # --- ai_office_reports ---
    op.execute("""
        CREATE TABLE ai_office_reports (
            id SERIAL PRIMARY KEY,
            agent VARCHAR(20) NOT NULL,
            report_type VARCHAR(30) NOT NULL,
            report_text TEXT NOT NULL,
            metadata JSONB DEFAULT '{}',
            telegram_sent BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_aor_agent ON ai_office_reports(agent)")
    op.execute("CREATE INDEX idx_aor_created ON ai_office_reports(created_at DESC)")

    # --- scout_reports ---
    op.execute("""
        CREATE TABLE scout_reports (
            id SERIAL PRIMARY KEY,
            fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
            prediction_id INTEGER,
            verdict VARCHAR(10) NOT NULL,
            report_text TEXT NOT NULL,
            factors JSONB DEFAULT '{}',
            model_selection VARCHAR(30),
            model_odd NUMERIC(8,3),
            override_verdict VARCHAR(10),
            override_reason TEXT,
            actual_result VARCHAR(30),
            scout_correct BOOLEAN,
            created_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE(fixture_id)
        )
    """)
    op.execute("CREATE INDEX idx_sr_fixture ON scout_reports(fixture_id)")
    op.execute("CREATE INDEX idx_sr_verdict ON scout_reports(verdict)")

    # --- news_articles ---
    op.execute("""
        CREATE TABLE news_articles (
            id SERIAL PRIMARY KEY,
            title VARCHAR(300) NOT NULL,
            slug VARCHAR(300) NOT NULL UNIQUE,
            body TEXT NOT NULL,
            summary VARCHAR(500),
            category VARCHAR(20) NOT NULL,
            league_id INTEGER REFERENCES leagues(id),
            fixture_id INTEGER REFERENCES fixtures(id),
            home_team_name VARCHAR(100),
            away_team_name VARCHAR(100),
            sources JSONB DEFAULT '[]',
            status VARCHAR(20) DEFAULT 'draft',
            published_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_na_status ON news_articles(status)")
    op.execute("CREATE INDEX idx_na_published ON news_articles(published_at DESC)")
    op.execute("CREATE INDEX idx_na_league ON news_articles(league_id)")
    op.execute("CREATE INDEX idx_na_category ON news_articles(category)")

    # --- news_sources ---
    op.execute("""
        CREATE TABLE news_sources (
            id SERIAL PRIMARY KEY,
            url VARCHAR(1000) NOT NULL UNIQUE,
            source_name VARCHAR(100),
            title VARCHAR(500),
            raw_text TEXT,
            processed BOOLEAN DEFAULT false,
            article_id INTEGER REFERENCES news_articles(id),
            fetched_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_ns_url ON news_sources(url)")
    op.execute("CREATE INDEX idx_ns_processed ON news_sources(processed)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS news_sources CASCADE")
    op.execute("DROP TABLE IF EXISTS news_articles CASCADE")
    op.execute("DROP TABLE IF EXISTS scout_reports CASCADE")
    op.execute("DROP TABLE IF EXISTS ai_office_reports CASCADE")
