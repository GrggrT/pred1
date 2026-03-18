import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { fetchLeagues } from '@/lib/api';
import { LeaguePill } from '@/components/leagues/LeaguePill';
import { Breadcrumbs } from '@/components/seo/Breadcrumbs';
import { JsonLd, breadcrumbJsonLd } from '@/components/seo/JsonLd';
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
    alternates[loc] = `/${loc}/leagues`;
  }

  return {
    title: t('leaguesTitle'),
    description: t('leaguesDescription'),
    alternates: {
      canonical: `/${locale}/leagues`,
      languages: alternates,
    },
    openGraph: {
      type: 'website',
      title: t('leaguesTitle'),
      description: t('leaguesDescription'),
      url: `${SITE_URL}/${locale}/leagues`,
      siteName: t('siteName'),
    },
  };
}

export default async function LeaguesPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'meta' });
  const tc = await getTranslations({ locale, namespace: 'common' });

  const leagues = await fetchLeagues().catch(() => []);

  const breadcrumbs = [
    { name: tc('home'), url: `${SITE_URL}/${locale}` },
    { name: t('leaguesTitle'), url: `${SITE_URL}/${locale}/leagues` },
  ];

  return (
    <>
      <JsonLd data={breadcrumbJsonLd(breadcrumbs)} />

      <div className={styles.container}>
        <Breadcrumbs items={[
          { label: tc('home'), href: '/' },
          { label: t('leaguesTitle') },
        ]} />

        <h1 className={styles.title}>{t('leaguesTitle')}</h1>
        <p className={styles.description}>{t('leaguesDescription')}</p>

        <div className={styles.grid}>
          {leagues.map((league) => (
            <LeaguePill key={league.id} league={league} />
          ))}
        </div>

        {leagues.length === 0 && (
          <p className={styles.noData}>{tc('noData')}</p>
        )}
      </div>
    </>
  );
}
