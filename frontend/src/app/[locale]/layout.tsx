import type { ReactNode } from 'react';
import type { Metadata } from 'next';
import { Sora, DM_Sans, JetBrains_Mono } from 'next/font/google';
import { NextIntlClientProvider } from 'next-intl';
import { getMessages, setRequestLocale } from 'next-intl/server';
import { locales, type Locale } from '@/i18n/config';
import { Header } from '@/components/layout/Header';
import { Footer } from '@/components/layout/Footer';
import { GoogleAnalytics } from '@/components/analytics/GoogleAnalytics';
import { YandexMetrika } from '@/components/analytics/YandexMetrika';
import '@/styles/globals.css';

const sora = Sora({
  subsets: ['latin', 'latin-ext'],
  variable: '--font-display',
  display: 'swap',
  weight: ['400', '500', '600', '700'],
});

const dmSans = DM_Sans({
  subsets: ['latin', 'latin-ext'],
  variable: '--font-body',
  display: 'swap',
  weight: ['400', '500', '600', '700'],
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin', 'latin-ext'],
  variable: '--font-mono',
  display: 'swap',
  weight: ['400', '500', '600', '700'],
});

export function generateStaticParams() {
  return locales.map((locale) => ({ locale }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const isRu = locale === 'ru';

  const alternates: Record<string, string> = {};
  for (const loc of locales) {
    alternates[loc] = `/${loc}`;
  }

  return {
    metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || 'https://footballvaluebets.com'),
    title: {
      default: isRu
        ? 'Football Value Bets — AI-прогнозы на футбол'
        : 'Football Value Bets — AI Football Predictions',
      template: '%s | Football Value Bets',
    },
    description: isRu
      ? 'Прогнозы на футбол с искусственным интеллектом. Точные предсказания на основе статистических моделей.'
      : 'AI-powered football predictions. Accurate forecasts based on statistical models and machine learning.',
    alternates: {
      languages: alternates,
    },
    openGraph: {
      type: 'website',
      locale: locale === 'ru' ? 'ru_RU' : locale === 'en' ? 'en_US' : locale,
      siteName: 'Football Value Bets',
    },
    robots: {
      index: true,
      follow: true,
    },
  };
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale as Locale);
  const messages = await getMessages();

  return (
    <html lang={locale} className={`dark ${sora.variable} ${dmSans.variable} ${jetbrainsMono.variable}`}>
      <head>
        <meta name="theme-color" content="#0a0b14" />
        <link rel="icon" href="/favicon.ico" sizes="any" />
      </head>
      <body>
        <NextIntlClientProvider messages={messages}>
          <Header locale={locale as Locale} />
          <main className="main-content">
            {children}
          </main>
          <Footer />
        </NextIntlClientProvider>
        <GoogleAnalytics />
        <YandexMetrika />
      </body>
    </html>
  );
}
