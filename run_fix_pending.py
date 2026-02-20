#!/usr/bin/env python3
"""
Script to fix PENDING fixtures in the database.
Run this to update fixtures that should be marked as finished.
"""
import asyncio
import sys
from pathlib import Path

# Add the app directory to the path so we can import modules
sys.path.insert(0, str(Path(__file__).parent))

from app.core.db import SessionLocal, init_db
from sqlalchemy import text


async def fix_pending_fixtures():
    """Fix fixtures with PENDING/UNK/NS status that should be FT."""
    await init_db()

    async with SessionLocal() as session:
        # First, show what we're about to fix
        print("🔍 Looking for fixtures to fix...")

        result = await session.execute(text("""
            SELECT
                id,
                kickoff,
                status,
                home_goals,
                away_goals,
                (kickoff < now() - interval '3 hours') as should_be_finished
            FROM fixtures
            WHERE status IN ('PENDING', 'UNK', 'NS')
              AND home_goals IS NOT NULL
              AND away_goals IS NOT NULL
              AND kickoff < now() - interval '3 hours'
            ORDER BY kickoff DESC
            LIMIT 10
        """))

        fixtures_to_fix = result.fetchall()

        if not fixtures_to_fix:
            print("✅ No fixtures need fixing!")
            return

        print(f"📊 Found {len(fixtures_to_fix)} fixtures to fix:")
        for fixture in fixtures_to_fix:
            print(f"  ID {fixture.id}: {fixture.status} -> FT "
                  f"({fixture.home_goals}-{fixture.away_goals}) "
                  f"kicked off {fixture.kickoff}")

        # Get total count
        count_result = await session.execute(text("""
            SELECT COUNT(*) as total
            FROM fixtures
            WHERE status IN ('PENDING', 'UNK', 'NS')
              AND home_goals IS NOT NULL
              AND away_goals IS NOT NULL
              AND kickoff < now() - interval '3 hours'
        """))
        total_count = count_result.scalar()

        print(f"\n🔧 About to fix {total_count} fixtures...")

        # Apply the fix
        update_result = await session.execute(text("""
            UPDATE fixtures
            SET
                status = 'FT',
                updated_at = now()
            WHERE status IN ('PENDING', 'UNK', 'NS')
              AND home_goals IS NOT NULL
              AND away_goals IS NOT NULL
              AND kickoff < now() - interval '3 hours'
        """))

        await session.commit()

        updated_count = update_result.rowcount
        print(f"✅ Successfully updated {updated_count} fixtures to 'FT' status!")


if __name__ == "__main__":
    print("🚀 Starting fixture status repair...")
    asyncio.run(fix_pending_fixtures())
    print("🎉 Done!")