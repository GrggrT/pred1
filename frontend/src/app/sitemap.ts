import type { MetadataRoute } from 'next';
import { fetchLeagues, fetchNewsSlugs } from '@/lib/api';
import { locales } from '@/i18n/config';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://footballvaluebets.com';

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const [leagues, newsSlugs] = await Promise.all([
    fetchLeagues().catch(() => []),
    fetchNewsSlugs().catch(() => []),
  ]);

  const urls: MetadataRoute.Sitemap = [];

  for (const locale of locales) {
    // Homepage
    urls.push({
      url: `${SITE_URL}/${locale}`,
      lastModified: new Date(),
      changeFrequency: 'daily',
      priority: 1.0,
    });

    // News index
    urls.push({
      url: `${SITE_URL}/${locale}/news`,
      lastModified: new Date(),
      changeFrequency: 'hourly',
      priority: 0.8,
    });

    // Leagues index
    urls.push({
      url: `${SITE_URL}/${locale}/leagues`,
      lastModified: new Date(),
      changeFrequency: 'weekly',
      priority: 0.7,
    });

    // About page
    urls.push({
      url: `${SITE_URL}/${locale}/about`,
      lastModified: new Date(),
      changeFrequency: 'monthly',
      priority: 0.5,
    });

    // Individual leagues
    for (const league of leagues) {
      if (league.slug) {
        urls.push({
          url: `${SITE_URL}/${locale}/leagues/${league.slug}`,
          lastModified: new Date(),
          changeFrequency: 'daily',
          priority: 0.6,
        });
      }
    }

    // Individual news articles
    for (const article of newsSlugs) {
      urls.push({
        url: `${SITE_URL}/${locale}/news/${article.slug}`,
        lastModified: article.published_at ? new Date(article.published_at) : new Date(),
        changeFrequency: 'weekly',
        priority: 0.5,
      });
    }
  }

  return urls;
}
