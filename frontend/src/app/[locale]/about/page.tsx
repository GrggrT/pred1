import type { Metadata } from 'next';
import { getTranslations, setRequestLocale } from 'next-intl/server';
import { fetchStats } from '@/lib/api';
import { Breadcrumbs } from '@/components/seo/Breadcrumbs';
import { JsonLd, breadcrumbJsonLd, organizationJsonLd } from '@/components/seo/JsonLd';
import { locales } from '@/i18n/config';
import styles from './page.module.css';

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || 'https://footballvaluebets.com';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'meta' });

  const alternates: Record<string, string> = {};
  for (const loc of locales) {
    alternates[loc] = `/${loc}/about`;
  }

  return {
    title: t('aboutTitle'),
    description: t('aboutDescription'),
    alternates: {
      canonical: `/${locale}/about`,
      languages: alternates,
    },
  };
}

const PIPELINE_STEPS = [1, 2, 3, 4, 5, 6] as const;

export default async function AboutPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations({ locale, namespace: 'about' });
  const tc = await getTranslations({ locale, namespace: 'common' });
  const tm = await getTranslations({ locale, namespace: 'meta' });

  const stats = await fetchStats(90).catch(() => null);

  const breadcrumbsData = [
    { name: tc('home'), url: `${SITE_URL}/${locale}` },
    { name: tm('aboutTitle'), url: `${SITE_URL}/${locale}/about` },
  ];

  return (
    <>
      <JsonLd data={breadcrumbJsonLd(breadcrumbsData)} />
      <JsonLd data={organizationJsonLd(SITE_URL)} />

      <div className={styles.container}>
        <Breadcrumbs items={[
          { label: tc('home'), href: '/' },
          { label: tm('aboutTitle') },
        ]} />

        <h1 className={styles.title}>{t('heading')}</h1>
        <p className={styles.intro}>{t('intro')}</p>

        {/* Track Record */}
        {stats && (
          <div className={styles.statsStrip}>
            <div className={styles.statItem}>
              <span className={styles.statValue}>{stats.total_bets}</span>
              <span className={styles.statLabel}>Total</span>
            </div>
            <div className={styles.statItem}>
              <span className={styles.statValue}>{stats.wins}</span>
              <span className={styles.statLabel}>Wins</span>
            </div>
            <div className={styles.statItem}>
              <span className={`${styles.statValue} ${stats.roi >= 0 ? styles.positive : styles.negative}`}>
                {stats.roi > 0 ? '+' : ''}{stats.roi.toFixed(1)}%
              </span>
              <span className={styles.statLabel}>ROI</span>
            </div>
            <div className={styles.statItem}>
              <span className={`${styles.statValue} ${stats.total_profit >= 0 ? styles.positive : styles.negative}`}>
                {stats.total_profit > 0 ? '+' : ''}{stats.total_profit.toFixed(1)}u
              </span>
              <span className={styles.statLabel}>Profit</span>
            </div>
          </div>
        )}

        {/* How It Works */}
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('howItWorksTitle')}</h2>
          <div className={styles.pipeline}>
            {PIPELINE_STEPS.map((step) => (
              <div key={step} className={styles.pipelineStep}>
                <div className={styles.stepNumber}>{step}</div>
                <div className={styles.stepName}>{t(`step${step}Name`)}</div>
                <div className={styles.stepDesc}>{t(`step${step}Desc`)}</div>
              </div>
            ))}
          </div>
        </section>

        {/* Leagues */}
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('leaguesTitle')}</h2>
          <p className={styles.text}>{t('leaguesText')}</p>
        </section>

        {/* Technology */}
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>{t('technologyTitle')}</h2>
          <p className={styles.text}>{t('technologyText')}</p>
        </section>

        {/* Disclaimer */}
        <section className={styles.disclaimer}>
          <h2 className={styles.sectionTitle}>{t('disclaimerTitle')}</h2>
          <p className={styles.text}>{t('disclaimerText')}</p>
        </section>
      </div>
    </>
  );
}
