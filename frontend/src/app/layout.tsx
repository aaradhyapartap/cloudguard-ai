import type { Metadata } from 'next';
import { IBM_Plex_Mono, IBM_Plex_Sans } from 'next/font/google';
import { AuthProvider } from '@/components/auth/AuthProvider';
import './globals.css';

/*
  IBM Plex rather than the usual geometric-sans default. Plex was drawn for
  technical documentation and instrumentation, which is what this product is —
  and the Sans/Mono pair share proportions, so an identifier set in mono sits
  correctly beside prose set in sans. Self-hosted by next/font, so no runtime
  request to a font CDN and no layout shift.
*/
const plexSans = IBM_Plex_Sans({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-plex-sans',
  display: 'swap',
});

const plexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500'],
  variable: '--font-plex-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'CloudGuard AI',
  description: 'Agentic audit and compliance intelligence',
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body className="min-h-screen antialiased">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
