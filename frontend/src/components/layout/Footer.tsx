import { useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import styles from './Footer.module.css';

export function Footer() {
  const t = useTranslations('footer');
  const nav = useTranslations('nav');
  const year = new Date().getFullYear();

  return (
    <footer className={styles.footer}>
      <div className={styles.inner}>
        <nav className={styles.links}>
          <Link href="/" className={styles.link}>{nav('home')}</Link>
          <Link href="/news" className={styles.link}>{nav('news')}</Link>
          <Link href="/leagues" className={styles.link}>{nav('leagues')}</Link>
          <Link href="/about" className={styles.link}>{nav('about')}</Link>
        </nav>
        <p className={styles.disclaimer}>{t('disclaimer')}</p>
        <p className={styles.copyright}>{t('copyright', { year })}</p>
      </div>
    </footer>
  );
}
