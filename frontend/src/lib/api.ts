/**
 * Server-side API client for FastAPI backend.
 * All functions are meant to run in Server Components (no 'use client').
 */

const API_BASE = process.env.API_URL || 'http://localhost:8000';

async function apiFetch<T>(path: string, revalidate = 300): Promise<T> {
  const url = `${API_BASE}/api/public/v1${path}`;
  const res = await fetch(url, {
    next: { revalidate },
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${url}`);
  }
  return res.json();
}

// ---------- Types ----------

export interface League {
  id: number;
  name: string;
  country: string;
  logo_url: string;
  slug: string;
}

export interface NewsArticle {
  id: number;
  title: string;
  slug: string;
  summary: string | null;
  body: string;
  category: string;
  league_id: number | null;
  fixture_id?: number | null;
  home_team_name?: string | null;
  away_team_name?: string | null;
  sources: any[];
  published_at: string | null;
  created_at?: string | null;
}

export interface NewsSlug {
  slug: string;
  published_at: string | null;
}

export interface Match {
  fixture_id: number;
  league_id: number;
  league?: string;
  home: string;
  away: string;
  home_logo_url?: string;
  away_logo_url?: string;
  kickoff: string;
  market: string;
  pick: string;
  odd: number;
  ev: number;
  confidence?: number;
  prob_home?: number | null;
  prob_draw?: number | null;
  prob_away?: number | null;
  prob_source?: string | null;
  fair_odd?: number | null;
}

export interface StatsData {
  period_days: number;
  total_bets: number;
  wins: number;
  losses: number;
  win_rate: number;
  roi: number;
  total_profit: number;
}

export interface MarketStats {
  [market: string]: {
    total: number;
    wins: number;
    profit: number;
    roi: number;
    win_rate: number;
  };
}

export interface StandingsRow {
  rank: number;
  team_id: number;
  team_name: string;
  team_logo_url: string;
  points: number;
  played: number;
  goals_for: number;
  goals_against: number;
  goal_diff: number;
  form: string | null;
}

/** Human-readable market labels */
export const MARKET_LABELS: Record<string, string> = {
  '1X2': '1X2',
  'TOTAL': 'Total',
  'TOTAL_OVER_2_5': 'T 2.5+',
  'TOTAL_UNDER_2_5': 'T 2.5−',
  'TOTAL_OVER_1_5': 'T 1.5+',
  'TOTAL_OVER_3_5': 'T 3.5+',
  'TOTAL_1_5': 'T 1.5+',
  'TOTAL_3_5': 'T 3.5+',
  'BTTS': 'BTTS',
  'BTTS_YES': 'BTTS',
  'BTTS_NO': 'BTTS No',
  'DOUBLE_CHANCE': 'DC',
  'DC': 'DC',
};

/** Human-readable pick labels */
export const PICK_LABELS: Record<string, string> = {
  'Home': '1',
  'Draw': 'X',
  'Away': '2',
  'HOME_WIN': '1',
  'AWAY_WIN': '2',
  'DRAW': 'X',
  'DC_1X': '1X',
  'DC_X2': 'X2',
  'DC_12': '12',
  'OVER_2_5': 'Over 2.5',
  'UNDER_2_5': 'Under 2.5',
  'OVER_1_5': 'Over 1.5',
  'OVER_3_5': 'Over 3.5',
  'UNDER_1_5': 'Under 1.5',
  'UNDER_3_5': 'Under 3.5',
  'YES': 'Yes',
  'NO': 'No',
};

/** Get human-readable market label (function to prevent bundler tree-shaking) */
export function getMarketLabel(market: string): string {
  const label = MARKET_LABELS[market];
  return label !== undefined ? label : market;
}

/** Get human-readable pick label (function to prevent bundler tree-shaking) */
export function getPickLabel(pick: string): string {
  const label = PICK_LABELS[pick];
  return label !== undefined ? label : pick;
}

export interface ResultRow {
  fixture_id: number;
  league_id: number;
  league_name: string;
  home_team: string;
  away_team: string;
  home_logo: string;
  away_logo: string;
  kickoff: string;
  market: string;
  pick: string;
  odd: number;
  ev: number;
  status: string;
  profit: number;
  teams: string;
}

// ---------- Fetchers ----------

export async function fetchLeagues(): Promise<League[]> {
  return apiFetch<League[]>('/leagues', 300);
}

export async function fetchLeagueBySlug(slug: string): Promise<League> {
  return apiFetch<League>(`/leagues/${slug}`, 600);
}

export async function fetchNews(limit = 10, category?: string): Promise<{ items: NewsArticle[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (category) params.set('category', category);
  return apiFetch<{ items: NewsArticle[] }>(`/news?${params}`, 60);
}

export async function fetchNewsBySlug(slug: string): Promise<NewsArticle> {
  return apiFetch<NewsArticle>(`/news/${slug}`, 60);
}

export async function fetchNewsSlugs(): Promise<NewsSlug[]> {
  return apiFetch<NewsSlug[]>('/news/slugs', 300);
}

export async function fetchMatches(params?: {
  league_id?: number;
  days_ahead?: number;
  limit?: number;
}): Promise<Match[]> {
  const sp = new URLSearchParams();
  if (params?.league_id) sp.set('league_id', String(params.league_id));
  if (params?.days_ahead) sp.set('days_ahead', String(params.days_ahead));
  if (params?.limit) sp.set('limit', String(params.limit));
  const query = sp.toString();
  return apiFetch<Match[]>(`/matches${query ? '?' + query : ''}`, 120);
}

export async function fetchStats(days = 90): Promise<StatsData> {
  return apiFetch<StatsData>(`/stats?days=${days}`, 120);
}

export async function fetchMarketStats(days = 90): Promise<MarketStats> {
  return apiFetch<MarketStats>(`/market-stats?days=${days}`, 120);
}

export async function fetchStandings(leagueId: number, season?: number): Promise<StandingsRow[]> {
  const params = new URLSearchParams({ league_id: String(leagueId) });
  if (season) params.set('season', String(season));
  return apiFetch<StandingsRow[]>(`/standings?${params}`, 300);
}

export async function fetchResults(params?: {
  league_id?: number;
  days?: number;
  limit?: number;
}): Promise<ResultRow[]> {
  const sp = new URLSearchParams();
  if (params?.league_id) sp.set('league_id', String(params.league_id));
  if (params?.days) sp.set('days', String(params.days));
  if (params?.limit) sp.set('limit', String(params.limit));
  const query = sp.toString();
  const res = await apiFetch<any>(`/results${query ? '?' + query : ''}`, 120);
  return res.data || res;
}
