-- Fix fixtures with PENDING status that should be finished
-- This script identifies and fixes matches that have:
-- 1. Status = 'PENDING' or 'UNK' or 'NS'
-- 2. Have goal scores (indicating the match was played)
-- 3. Kickoff time > 3 hours ago (match should be finished by now)

-- First, let's see what we're dealing with
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
ORDER BY kickoff DESC;

-- Update these fixtures to 'FT' status
UPDATE fixtures
SET
    status = 'FT',
    updated_at = now()
WHERE status IN ('PENDING', 'UNK', 'NS')
  AND home_goals IS NOT NULL
  AND away_goals IS NOT NULL
  AND kickoff < now() - interval '3 hours';

-- Report on what was updated
SELECT COUNT(*) as fixed_fixtures_count
FROM fixtures
WHERE status = 'FT'
  AND home_goals IS NOT NULL
  AND away_goals IS NOT NULL
  AND kickoff < now() - interval '3 hours'
  AND updated_at > now() - interval '1 minute';