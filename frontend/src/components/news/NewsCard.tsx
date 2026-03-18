import { Link } from '@/i18n/navigation';
import type { NewsArticle } from '@/lib/api';
import styles from './NewsCard.module.css';

interface Props {
  article: NewsArticle;
}

const CAT_COLORS: Record<string, string> = {
  preview: '#38bdf8',
  review: '#22c55e',
  injury: '#ef4444',
  transfer: '#f59e0b',
  standings: '#a78bfa',
};

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '';
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

export function NewsCard({ article }: Props) {
  const catColor = CAT_COLORS[article.category] || '#64748b';

  return (
    <Link href={`/news/${article.slug}`} className={styles.card}>
      <div className={styles.catDot} style={{ backgroundColor: catColor }} />
      <div className={styles.content}>
        <h3 className={styles.title}>{article.title}</h3>
        {article.summary && (
          <p className={styles.summary}>{article.summary}</p>
        )}
        <div className={styles.meta}>
          <span className={styles.category} style={{ color: catColor }}>
            {article.category}
          </span>
          <span className={styles.time}>{timeAgo(article.published_at)}</span>
        </div>
      </div>
    </Link>
  );
}
