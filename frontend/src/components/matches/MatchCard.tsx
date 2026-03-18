import Image from 'next/image';
import type { Match } from '@/lib/api';
import { getMarketLabel, getPickLabel } from '@/lib/api';
import { ProbBar } from './ProbBar';
import styles from './MatchCard.module.css';

interface Props {
  match: Match;
  locale: string;
}

function formatKickoff(iso: string, locale: string): { date: string; time: string } {
  const d = new Date(iso);
  const time = d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
  const date = d.toLocaleDateString(locale, { day: 'numeric', month: 'short' });
  return { date, time };
}

function evClass(ev: number): string {
  if (ev >= 10) return styles.evHigh;
  if (ev >= 5) return styles.evMid;
  return styles.evLow;
}

export function MatchCard({ match, locale }: Props) {
  const { date, time } = formatKickoff(match.kickoff, locale);
  const marketLabel = getMarketLabel(match.market);
  const pickLabel = getPickLabel(match.pick);

  return (
    <article className={styles.card}>
      {/* Header: time + EV tag */}
      <div className={styles.header}>
        <span className={styles.time}>{time}</span>
        <span className={styles.date}>{date}</span>
        {match.ev > 0 && (
          <span className={`${styles.evTag} ${evClass(match.ev)}`}>
            EV {match.ev.toFixed(1)}%
          </span>
        )}
      </div>

      {/* Teams row */}
      <div className={styles.teams}>
        <div className={styles.team}>
          {match.home_logo_url && (
            <Image src={match.home_logo_url} alt="" className={styles.logo} width={28} height={28} unoptimized />
          )}
          <span className={styles.teamName}>{match.home}</span>
        </div>
        <span className={styles.vs}>vs</span>
        <div className={`${styles.team} ${styles.away}`}>
          <span className={styles.teamName}>{match.away}</span>
          {match.away_logo_url && (
            <Image src={match.away_logo_url} alt="" className={styles.logo} width={28} height={28} unoptimized />
          )}
        </div>
      </div>

      {/* Probability bar (1X2 only) */}
      {match.prob_home != null && match.prob_draw != null && match.prob_away != null && (
        <ProbBar home={match.prob_home} draw={match.prob_draw} away={match.prob_away} />
      )}

      {/* Pick row */}
      <div className={styles.pickRow}>
        <span className={styles.pickMarket}>{marketLabel}</span>
        <span className={styles.pickLabel}>{pickLabel}</span>
        <span className={styles.pickOdd}>{match.odd.toFixed(2)}</span>
      </div>
    </article>
  );
}
