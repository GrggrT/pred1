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
  meta_description?: string | null;
  published_at?: string | null;
  slug: string;
  tags?: string[] | null;
  category?: string;
  word_count?: number | null;
  author?: string | null;
  image_url?: string | null;
}, locale: string, siteUrl: string) {
  return {
    '@context': 'https://schema.org',
    '@type': 'NewsArticle',
    headline: article.title,
    description: article.meta_description || article.summary || '',
    datePublished: article.published_at || undefined,
    url: `${siteUrl}/${locale}/news/${article.slug}`,
    inLanguage: locale,
    author: {
      '@type': 'Organization',
      name: article.author || 'Football Value Bets',
      url: siteUrl,
    },
    publisher: {
      '@type': 'Organization',
      name: 'Football Value Bets',
      url: siteUrl,
    },
    image: article.image_url || `${siteUrl}/${locale}/news/${article.slug}/opengraph-image`,
    keywords: article.tags?.join(', ') || undefined,
    articleSection: article.category || undefined,
    wordCount: article.word_count || undefined,
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
