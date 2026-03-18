import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import type { Locale } from '@/i18n/config';
import { LanguageSwitcher } from './LanguageSwitcher';
import styles from './Header.module.css';

interface HeaderProps {
  locale: Locale;
}

export function Header({ locale }: HeaderProps) {
  const t = useTranslations('nav');

  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <Link href="/" className={styles.logo}>
          <span className={styles.logoMark}>FVB</span>
          <span className={styles.logoSub}>Football Value Bets</span>
        </Link>

        <nav className={styles.nav}>
          <Link href="/" className={styles.navLink}>
            {t('home')}
          </Link>
          <Link href="/news" className={styles.navLink}>
            {t('news')}
          </Link>
          <Link href="/leagues" className={styles.navLink}>
            {t('leagues')}
          </Link>
          <Link href="/about" className={styles.navLink}>
            {t('about')}
          </Link>
        </nav>

        <LanguageSwitcher locale={locale} />
      </div>
    </header>
  );
}
