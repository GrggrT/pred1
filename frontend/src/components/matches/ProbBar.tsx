import styles from './ProbBar.module.css';

interface Props {
  home: number;
  draw: number;
  away: number;
}

export function ProbBar({ home, draw, away }: Props) {
  const h = Math.round(home * 100);
  const d = Math.round(draw * 100);
  const a = 100 - h - d;

  return (
    <div className={styles.wrapper}>
      <div className={styles.bar}>
        <div className={styles.home} style={{ width: `${h}%` }}>
          {h > 10 && <span>{h}%</span>}
        </div>
        <div className={styles.draw} style={{ width: `${d}%` }}>
          {d > 10 && <span>{d}%</span>}
        </div>
        <div className={styles.away} style={{ width: `${a}%` }}>
          {a > 10 && <span>{a}%</span>}
        </div>
      </div>
    </div>
  );
}
