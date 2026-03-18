import { useTranslations } from 'next-intl';
import type { NewsArticle } from '@/lib/api';
import { NewsCard } from './NewsCard';

interface Props {
  articles: NewsArticle[];
}

export function NewsList({ articles }: Props) {
  const t = useTranslations('common');

  if (!articles.length) {
    return <p style={{ color: 'var(--muted)', fontSize: 'var(--font-size-sm)' }}>{t('noData')}</p>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
      {articles.map((article) => (
        <NewsCard key={article.id} article={article} />
      ))}
    </div>
  );
}
