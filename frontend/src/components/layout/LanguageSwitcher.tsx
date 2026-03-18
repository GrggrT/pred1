'use client';

import { usePathname, useRouter } from '@/i18n/navigation';
import { locales, localeNames, type Locale } from '@/i18n/config';
import { useState, useRef, useEffect } from 'react';
import styles from './LanguageSwitcher.module.css';

interface Props {
  locale: Locale;
}

export function LanguageSwitcher({ locale }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  function switchLocale(newLocale: Locale) {
    router.replace(pathname, { locale: newLocale });
    setOpen(false);
  }

  return (
    <div className={styles.wrapper} ref={ref}>
      <button
        className={styles.trigger}
        onClick={() => setOpen(!open)}
        aria-label="Change language"
      >
        {locale.toUpperCase()}
      </button>
      {open && (
        <ul className={styles.dropdown}>
          {locales.map((loc) => (
            <li key={loc}>
              <button
                className={`${styles.option} ${loc === locale ? styles.active : ''}`}
                onClick={() => switchLocale(loc)}
              >
                <span className={styles.code}>{loc.toUpperCase()}</span>
                <span className={styles.name}>{localeNames[loc]}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
