import { ImageResponse } from 'next/og';

export const runtime = 'nodejs';
export const alt = 'Football Value Bets — AI Football Predictions';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';

export default function OGImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'linear-gradient(135deg, #0a0b14 0%, #131a2c 50%, #0e1221 100%)',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          gap: '30px',
        }}
      >
        {/* Logo mark */}
        <div
          style={{
            width: '80px',
            height: '80px',
            borderRadius: '16px',
            background: '#b6f33d',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '44px',
            fontWeight: 800,
            color: '#0a0b14',
          }}
        >
          F
        </div>

        {/* Title */}
        <div
          style={{
            fontSize: '56px',
            fontWeight: 700,
            color: '#e2e8f0',
            letterSpacing: '-1px',
          }}
        >
          Football Value Bets
        </div>

        {/* Subtitle */}
        <div
          style={{
            fontSize: '26px',
            color: '#94a3b8',
            fontWeight: 400,
          }}
        >
          AI-powered football predictions & analytics
        </div>

        {/* Feature pills */}
        <div style={{ display: 'flex', gap: '16px', marginTop: '10px' }}>
          {['Dixon-Coles', 'ELO Ratings', 'Stacking ML', '6 Leagues'].map(
            (label) => (
              <div
                key={label}
                style={{
                  padding: '8px 20px',
                  borderRadius: '8px',
                  border: '1px solid rgba(182, 243, 61, 0.3)',
                  color: '#b6f33d',
                  fontSize: '16px',
                  fontWeight: 600,
                }}
              >
                {label}
              </div>
            ),
          )}
        </div>

        {/* Accent line at top */}
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            height: '4px',
            background: 'linear-gradient(90deg, #b6f33d, #22c55e, #38bdf8)',
          }}
        />
      </div>
    ),
    { ...size },
  );
}
