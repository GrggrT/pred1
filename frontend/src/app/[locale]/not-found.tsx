import { Link } from '@/i18n/navigation';

export default function NotFound() {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: 'calc(100vh - 200px)',
      gap: '16px',
    }}>
      <h1 style={{
        fontFamily: 'var(--font-display)',
        fontSize: '48px',
        fontWeight: 700,
        color: 'var(--muted)',
      }}>
        404
      </h1>
      <p style={{ color: 'var(--text-2)', fontSize: 'var(--font-size-lg)' }}>
        Page not found
      </p>
      <Link href="/" style={{
        color: 'var(--accent)',
        fontSize: 'var(--font-size-md)',
        fontWeight: 600,
      }}>
        Go Home
      </Link>
    </div>
  );
}
