import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { fetchNews } from '@/lib/api';
import { NewsCard } from '@/components/news/NewsCard';
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
    alternates[loc] = `/${loc}/news`;
  }

  return {
    title: t('newsTitle'),
    description: t('newsDescription'),
    alternates: {
      canonical: `/${locale}/news`,
      languages: alternates,
    },
    openGraph: {
      type: 'website',
      title: t('newsTitle'),
      description: t('newsDescription'),
      url: `${SITE_URL}/${locale}/news`,
      siteName: t('siteName'),
    },
  };
}

export default async function NewsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'meta' });
  const tc = await getTranslations({ locale, namespace: 'common' });

  const newsData = await fetchNews(30).catch(() => ({ items: [] }));

  const breadcrumbs = [
    { name: tc('home'), url: `${SITE_URL}/${locale}` },
    { name: t('newsTitle'), url: `${SITE_URL}/${locale}/news` },
  ];

  return (
    <>
      <JsonLd data={breadcrumbJsonLd(breadcrumbs)} />

      <div className={styles.container}>
        <Breadcrumbs items={[
          { label: tc('home'), href: '/' },
          { label: t('newsTitle') },
        ]} />

        <h1 className={styles.title}>{t('newsTitle')}</h1>
        <p className={styles.description}>{t('newsDescription')}</p>

        <div className={styles.grid}>
          {newsData.items.map((article) => (
            <NewsCard key={article.id} article={article} />
          ))}
        </div>

        {newsData.items.length === 0 && (
          <p className={styles.noData}>{tc('noData')}</p>
        )}
      </div>
    </>
  );
}
