import { ImageResponse } from 'next/og';
import { fetchNewsBySlug } from '@/lib/api';

export const runtime = 'nodejs';
export const alt = 'Football Value Bets';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

const CAT_COLORS: Record<string, string> = {
  preview: '#38bdf8',
  review: '#22c55e',
  injury: '#ef4444',
  transfer: '#f59e0b',
  standings: '#a78bfa',
};

export default async function OGImage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;

  let title = 'Football Value Bets';
  let category = '';
  let author = 'FVB AI Analytics';
  let readingTime = 0;

  try {
    const article = await fetchNewsBySlug(slug);
    title = article.title;
    category = article.category || '';
    author = article.author || 'FVB AI Analytics';
    readingTime = article.reading_time || 0;
  } catch {
    // fallback to defaults
  }

  const catColor = CAT_COLORS[category] || '#b6f33d';

  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          padding: '60px 70px',
          background: 'linear-gradient(135deg, #0a0b14 0%, #131a2c 50%, #0e1221 100%)',
          fontFamily: 'system-ui, -apple-system, sans-serif',
        }}
      >
        {/* Top bar: category + reading time */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {category && (
            <div
              style={{
                padding: '6px 16px',
                borderRadius: '6px',
                background: catColor,
                color: '#0a0b14',
                fontSize: '18px',
                fontWeight: 700,
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              {category}
            </div>
          )}
          {readingTime > 0 && (
            <div style={{ color: '#94a3b8', fontSize: '18px' }}>
              {readingTime} min read
            </div>
          )}
        </div>

        {/* Title */}
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '20px',
            flex: 1,
            justifyContent: 'center',
          }}
        >
          <div
            style={{
              fontSize: title.length > 80 ? 36 : title.length > 50 ? 44 : 52,
              fontWeight: 700,
              color: '#e2e8f0',
              lineHeight: 1.2,
              letterSpacing: '-0.5px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {title}
          </div>
        </div>

        {/* Bottom bar: author + branding */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-end',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ color: '#64748b', fontSize: '16px' }}>by</div>
            <div style={{ color: '#94a3b8', fontSize: '20px', fontWeight: 600 }}>
              {author}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div
              style={{
                width: '36px',
                height: '36px',
                borderRadius: '8px',
                background: '#b6f33d',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '20px',
                fontWeight: 800,
                color: '#0a0b14',
              }}
            >
              F
            </div>
            <div style={{ color: '#b6f33d', fontSize: '22px', fontWeight: 700 }}>
              Football Value Bets
            </div>
          </div>
        </div>

        {/* Accent line at top */}
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            height: '4px',
            background: `linear-gradient(90deg, ${catColor}, #b6f33d)`,
          }}
        />
      </div>
    ),
    { ...size },
  );
}
