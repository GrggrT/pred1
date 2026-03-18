import type { Metadata } from 'next';
import Image from 'next/image';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import {
  fetchLeagues,
  fetchLeagueBySlug,
  fetchStandings,
  fetchMatches,
  fetchNews,
} from '@/lib/api';
import { StandingsTable } from '@/components/leagues/StandingsTable';
import { MatchCard } from '@/components/matches/MatchCard';
import { NewsList } from '@/components/news/NewsList';
import { Breadcrumbs } from '@/components/seo/Breadcrumbs';
import { JsonLd, breadcrumbJsonLd } from '@/components/seo/JsonLd';
import { locales } from '@/i18n/config';
import styles from './page.module.css';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://footballvaluebets.com';

export async function generateStaticParams() {
  try {
    const leagues = await fetchLeagues();
    const params = [];
    for (const locale of locales) {
      for (const league of leagues) {
        if (league.slug) {
          params.push({ locale, slug: league.slug });
        }
      }
    }
    return params;
  } catch {
    return [];
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}): Promise<Metadata> {
  const { locale, slug } = await params;

  try {
    const league = await fetchLeagueBySlug(slug);
    const t = await getTranslations({ locale, namespace: 'meta' });
    const title = `${league.name} — ${t('leaguesTitle')}`;

    const alternates: Record<string, string> = {};
    for (const loc of locales) {
      alternates[loc] = `/${loc}/leagues/${slug}`;
    }

    const description = `${league.name} (${league.country}) — standings, predictions, stats`;
    return {
      title,
      description,
      alternates: {
        canonical: `/${locale}/leagues/${slug}`,
        languages: alternates,
      },
      openGraph: {
        type: 'website',
        title,
        description,
        url: `${SITE_URL}/${locale}/leagues/${slug}`,
        siteName: 'Football Value Bets',
        images: league.logo_url ? [{ url: league.logo_url }] : undefined,
      },
    };
  } catch {
    return { title: 'League not found' };
  }
}

export default async function LeagueDetailPage({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  const { locale, slug } = await params;
  setRequestLocale(locale);

  let league;
  try {
    league = await fetchLeagueBySlug(slug);
  } catch {
    notFound();
  }

  const t = await getTranslations({ locale, namespace: 'meta' });
  const tc = await getTranslations({ locale, namespace: 'common' });
  const tn = await getTranslations({ locale, namespace: 'nav' });

  // Fetch league-specific data in parallel
  const [standings, matches, newsData] = await Promise.all([
    fetchStandings(league.id).catch(() => []),
    fetchMatches({ league_id: league.id, limit: 10 }).catch(() => []),
    fetchNews(5).catch(() => ({ items: [] })),
  ]);

  // Filter news for this league
  const leagueNews = newsData.items.filter(
    (n) => n.league_id === league.id
  );

  const breadcrumbs = [
    { name: tc('home'), url: `${SITE_URL}/${locale}` },
    { name: t('leaguesTitle'), url: `${SITE_URL}/${locale}/leagues` },
    { name: league.name, url: `${SITE_URL}/${locale}/leagues/${slug}` },
  ];

  return (
    <>
      <JsonLd data={breadcrumbJsonLd(breadcrumbs)} />
      <JsonLd data={{
        '@context': 'https://schema.org',
        '@type': 'SportsOrganization',
        name: league.name,
        sport: 'Football',
        url: `${SITE_URL}/${locale}/leagues/${slug}`,
      }} />

      <div className={styles.container}>
        <Breadcrumbs items={[
          { label: tc('home'), href: '/' },
          { label: t('leaguesTitle'), href: '/leagues' },
          { label: league.name },
        ]} />

        <header className={styles.header}>
          {league.logo_url && (
            <Image src={league.logo_url} alt="" className={styles.logo} width={40} height={40} unoptimized />
          )}
          <div>
            <h1 className={styles.title}>{league.name}</h1>
            <p className={styles.country}>{league.country}</p>
          </div>
        </header>

        <div className={styles.layout}>
          {/* Left: Standings + Predictions */}
          <div className={styles.main}>
            {/* Standings */}
            <section className={styles.section}>
              <h2 className={styles.sectionTitle}>{tc('standings')}</h2>
              <StandingsTable rows={standings} />
            </section>

            {/* Predictions */}
            {matches.length > 0 && (
              <section className={styles.section}>
                <h2 className={styles.sectionTitle}>{tc('predictions')}</h2>
                <div className={styles.matchesGrid}>
                  {matches.map((m) => (
                    <MatchCard key={m.fixture_id} match={m} locale={locale} />
                  ))}
                </div>
              </section>
            )}
          </div>

          {/* Right: News */}
          <aside className={styles.sidebar}>
            {leagueNews.length > 0 && (
              <section>
                <h2 className={styles.sectionTitle}>{tn('news')}</h2>
                <NewsList articles={leagueNews} />
              </section>
            )}
          </aside>
        </div>
      </div>
    </>
  );
}
