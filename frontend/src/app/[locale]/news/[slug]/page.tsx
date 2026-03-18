import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { fetchNewsBySlug, fetchNewsSlugs, fetchLeagues } from '@/lib/api';
import { Link } from '@/i18n/navigation';
import { Breadcrumbs } from '@/components/seo/Breadcrumbs';
import { JsonLd, newsArticleJsonLd, breadcrumbJsonLd } from '@/components/seo/JsonLd';
import { locales } from '@/i18n/config';
import styles from './page.module.css';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://footballvaluebets.com';

export async function generateStaticParams() {
  try {
    const slugs = await fetchNewsSlugs();
    const params = [];
    for (const locale of locales) {
      for (const s of slugs) {
        params.push({ locale, slug: s.slug });
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
    const article = await fetchNewsBySlug(slug);
    const alternates: Record<string, string> = {};
    for (const loc of locales) {
      alternates[loc] = `/${loc}/news/${slug}`;
    }

    return {
      title: article.title,
      description: article.summary || article.title,
      alternates: {
        canonical: `/${locale}/news/${slug}`,
        languages: alternates,
      },
      openGraph: {
        type: 'article',
        title: article.title,
        description: article.summary || article.title,
        publishedTime: article.published_at || undefined,
      },
    };
  } catch {
    return { title: 'Article not found' };
  }
}

export default async function NewsArticlePage({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  const { locale, slug } = await params;
  setRequestLocale(locale);

  let article;
  try {
    article = await fetchNewsBySlug(slug);
  } catch {
    notFound();
  }

  const t = await getTranslations({ locale, namespace: 'meta' });
  const tc = await getTranslations({ locale, namespace: 'common' });

  // Find league for internal linking
  let leagueLink: { name: string; slug: string } | null = null;
  if (article.league_id) {
    try {
      const leagues = await fetchLeagues();
      const match = leagues.find((l) => l.id === article.league_id);
      if (match?.slug) {
        leagueLink = { name: match.name, slug: match.slug };
      }
    } catch { /* ignore */ }
  }

  const breadcrumbs = [
    { name: tc('home'), url: `${SITE_URL}/${locale}` },
    { name: t('newsTitle'), url: `${SITE_URL}/${locale}/news` },
    { name: article.title, url: `${SITE_URL}/${locale}/news/${slug}` },
  ];

  const publishedDate = article.published_at
    ? new Date(article.published_at).toLocaleDateString(locale, {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      })
    : null;

  return (
    <>
      <JsonLd data={newsArticleJsonLd(article, locale, SITE_URL)} />
      <JsonLd data={breadcrumbJsonLd(breadcrumbs)} />

      <article className={styles.container}>
        <Breadcrumbs items={[
          { label: tc('home'), href: '/' },
          { label: t('newsTitle'), href: '/news' },
          { label: article.title },
        ]} />

        <header className={styles.header}>
          <span className={styles.category}>{article.category}</span>
          {publishedDate && <time className={styles.date}>{publishedDate}</time>}
        </header>

        <h1 className={styles.title}>{article.title}</h1>

        {article.summary && (
          <p className={styles.summary}>{article.summary}</p>
        )}

        {/* Teams context if match-related */}
        {article.home_team_name && article.away_team_name && (
          <div className={styles.matchContext}>
            <span>{article.home_team_name}</span>
            <span className={styles.vs}>vs</span>
            <span>{article.away_team_name}</span>
          </div>
        )}

        <div
          className={styles.body}
          dangerouslySetInnerHTML={{ __html: article.body }}
        />

        {leagueLink && (
          <Link href={`/leagues/${leagueLink.slug}`} className={styles.leagueLink}>
            {leagueLink.name} →
          </Link>
        )}
      </article>
    </>
  );
}
