interface Props {
  data: Record<string, any>;
}

export function JsonLd({ data }: Props) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
    />
  );
}

// --- Preset generators ---

export function websiteJsonLd(locale: string, siteUrl: string) {
  return {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    name: 'Football Value Bets',
    url: `${siteUrl}/${locale}`,
    inLanguage: locale,
    publisher: {
      '@type': 'Organization',
      name: 'Football Value Bets',
      url: siteUrl,
    },
  };
}

export function newsArticleJsonLd(article: {
  title: string;
  summary?: string | null;
  published_at?: string | null;
  slug: string;
}, locale: string, siteUrl: string) {
  return {
    '@context': 'https://schema.org',
    '@type': 'NewsArticle',
    headline: article.title,
    description: article.summary || '',
    datePublished: article.published_at || undefined,
    url: `${siteUrl}/${locale}/news/${article.slug}`,
    inLanguage: locale,
    publisher: {
      '@type': 'Organization',
      name: 'Football Value Bets',
    },
  };
}

export function organizationJsonLd(siteUrl: string) {
  return {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    name: 'Football Value Bets',
    url: siteUrl,
    description: 'AI-powered football predictions using statistical models and machine learning',
    foundingDate: '2025',
  };
}

export function breadcrumbJsonLd(
  items: Array<{ name: string; url: string }>,
) {
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: item.name,
      item: item.url,
    })),
  };
}
