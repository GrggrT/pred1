import Image from 'next/image';
import { useTranslations } from 'next-intl';
import type { StandingsRow } from '@/lib/api';
import styles from './StandingsTable.module.css';

interface Props {
  rows: StandingsRow[];
}

export function StandingsTable({ rows }: Props) {
  const t = useTranslations('standings');
  const tc = useTranslations('common');

  if (!rows.length) {
    return <p style={{ color: 'var(--muted)', fontSize: 'var(--font-size-sm)' }}>{tc('noData')}</p>;
  }

  return (
    <div className={styles.wrapper}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th className={styles.pos}>{t('position')}</th>
            <th className={styles.team}>{t('team')}</th>
            <th>{t('played')}</th>
            <th>{t('goalsFor')}</th>
            <th>{t('goalsAgainst')}</th>
            <th>{t('goalDiff')}</th>
            <th className={styles.pts}>{t('points')}</th>
            <th className={styles.formCol}>{t('form')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.team_id}>
              <td className={styles.pos}>{row.rank}</td>
              <td className={styles.team}>
                <div className={styles.teamCell}>
                  {row.team_logo_url && (
                    <Image src={row.team_logo_url} alt="" width={18} height={18} className={styles.logo} unoptimized />
                  )}
                  <span>{row.team_name}</span>
                </div>
              </td>
              <td>{row.played}</td>
              <td>{row.goals_for ?? '-'}</td>
              <td>{row.goals_against ?? '-'}</td>
              <td className={row.goal_diff > 0 ? styles.positive : row.goal_diff < 0 ? styles.negative : ''}>
                {row.goal_diff > 0 ? `+${row.goal_diff}` : row.goal_diff}
              </td>
              <td className={styles.pts}>{row.points}</td>
              <td className={styles.formCol}>
                {row.form && (
                  <div className={styles.formDots}>
                    {row.form.split('').slice(-5).map((ch, i) => (
                      <span
                        key={i}
                        className={`${styles.formDot} ${
                          ch === 'W' ? styles.win : ch === 'D' ? styles.drawDot : styles.loss
                        }`}
                      >
                        {ch}
                      </span>
                    ))}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
