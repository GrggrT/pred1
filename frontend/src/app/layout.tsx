import type { ReactNode } from 'react';

// Root layout — just redirects to default locale.
// All content is under /[locale]/.
export default function RootLayout({ children }: { children: ReactNode }) {
  return children;
}
