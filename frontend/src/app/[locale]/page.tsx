import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { fetchMatches, fetchStats, fetchMarketStats, fetchNews, fetchLeagues, getMarketLabel } from '@/lib/api';
import { MatchCard } from '@/components/matches/MatchCard';
import { NewsList } from '@/components/news/NewsList';
import { LeaguePill } from '@/components/leagues/LeaguePill';
import { JsonLd, websiteJsonLd } from '@/components/seo/JsonLd';
import { locales } from '@/i18n/config';
import styles from './page.module.css';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://footballvaluebets.com';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'meta' });

  const alternates: Record<string, string> = {};
  for (const loc of locales) {
    alternates[loc] = `/${loc}`;
  }

  return {
    title: t('homeTitle'),
    description: t('siteDescription'),
    alternates: {
      canonical: `/${locale}`,
      languages: alternates,
    },
    openGraph: {
      type: 'website',
      title: t('homeTitle'),
      description: t('siteDescription'),
      url: `${SITE_URL}/${locale}`,
      siteName: t('siteName'),
    },
  };
}

export default async function HomePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'home' });
  const tc = await getTranslations({ locale, namespace: 'common' });

  // Fetch all data in parallel
  const [matches, stats, marketStats, newsData, leagues] = await Promise.all([
    fetchMatches({ limit: 10 }).catch(() => []),
    fetchStats(90).catch(() => null),
    fetchMarketStats(90).catch(() => ({})),
    fetchNews(5).catch(() => ({ items: [] })),
    fetchLeagues().catch(() => []),
  ]);

  return (
    <>
      <JsonLd data={websiteJsonLd(locale, SITE_URL)} />

      <div className={styles.layout}>
        {/* Left sidebar — News */}
        <aside className={styles.sidebar}>
          <h2 className={styles.sectionTitle}>{t('latestNews')}</h2>
          <NewsList articles={newsData.items} />
        </aside>

        {/* Main content — Matches */}
        <div className={styles.main}>
          <section>
            <h1 className={styles.heroTitle}>{t('heroTitle')}</h1>
            <p className={styles.heroSub}>{t('heroSubtitle')}</p>
          </section>

          {/* Track Record strip */}
          {stats && (
            <div className={styles.statsStrip}>
              <div className={styles.statItem}>
                <span className={styles.statValue}>{stats.total_bets}</span>
                <span className={styles.statLabel}>Total</span>
              </div>
              <div className={styles.statItem}>
                <span className={styles.statValue}>{stats.wins}</span>
                <span className={styles.statLabel}>Wins</span>
              </div>
              <div className={styles.statItem}>
                <span className={`${styles.statValue} ${stats.roi >= 0 ? styles.positive : styles.negative}`}>
                  {stats.roi > 0 ? '+' : ''}{stats.roi.toFixed(1)}%
                </span>
                <span className={styles.statLabel}>ROI</span>
              </div>
              <div className={styles.statItem}>
                <span className={`${styles.statValue} ${stats.total_profit >= 0 ? styles.positive : styles.negative}`}>
                  {stats.total_profit > 0 ? '+' : ''}{stats.total_profit.toFixed(1)}u
                </span>
                <span className={styles.statLabel}>Profit</span>
              </div>
            </div>
          )}

          {/* Matches grid */}
          <section>
            <h2 className={styles.sectionTitle}>{t('todayMatches')}</h2>
            <div className={styles.matchesGrid}>
              {matches.length > 0 ? (
                matches.map((m) => (
                  <MatchCard key={m.fixture_id} match={m} locale={locale} />
                ))
              ) : (
                <p className={styles.noData}>{tc('noData')}</p>
              )}
            </div>
          </section>
        </div>

        {/* Right sidebar — Leagues */}
        <aside className={styles.rightSidebar}>
          <h2 className={styles.sectionTitle}>{t('marketStats')}</h2>
          {marketStats && Object.entries(marketStats).length > 0 && (
            <div className={styles.marketCards}>
              {Object.entries(marketStats).map(([market, data]: [string, any]) => (
                <div key={market} className={styles.marketCard}>
                  <span className={styles.marketName}>{getMarketLabel(market)}</span>
                  <span className={`${styles.marketRoi} ${data.roi >= 0 ? styles.positive : styles.negative}`}>
                    {data.roi > 0 ? '+' : ''}{data.roi?.toFixed(1)}%
                  </span>
                  <span className={styles.marketWin}>{data.win_rate?.toFixed(0)}% WR</span>
                </div>
              ))}
            </div>
          )}

          <h2 className={styles.sectionTitle} style={{ marginTop: 20 }}>{t('leagues')}</h2>
          <div className={styles.leaguesList}>
            {leagues.map((league) => (
              <LeaguePill key={league.id} league={league} />
            ))}
          </div>
        </aside>
      </div>
    </>
  );
}
