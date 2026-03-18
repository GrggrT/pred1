import Image from 'next/image';
import { Link } from '@/i18n/navigation';
import type { League } from '@/lib/api';
import styles from './LeaguePill.module.css';

interface Props {
  league: League;
}

export function LeaguePill({ league }: Props) {
  return (
    <Link href={`/leagues/${league.slug}`} className={styles.pill}>
      {league.logo_url && (
        <Image src={league.logo_url} alt="" className={styles.logo} width={20} height={20} unoptimized />
      )}
      <span className={styles.name}>{league.name}</span>
      <span className={styles.country}>{league.country}</span>
    </Link>
  );
}
