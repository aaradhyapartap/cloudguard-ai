'use client';

import { useEffect, useRef, useState } from 'react';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const mobileMenuButtonRef = useRef<HTMLButtonElement | null>(null);
  const backgroundRef = useRef<HTMLDivElement | null>(null);
  const wasMobileMenuOpenRef = useRef(false);
  const shouldRestoreFocusRef = useRef(false);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const mql = window.matchMedia('(min-width: 768px)');
    const syncDesktopBreakpoint = (matches: boolean) => {
      if (matches) {
        shouldRestoreFocusRef.current = false;
        setMobileMenuOpen(false);
      }
    };
    syncDesktopBreakpoint(mql.matches);
    const handleChange = (event: MediaQueryListEvent) => {
      syncDesktopBreakpoint(event.matches);
    };
    mql.addEventListener('change', handleChange);
    return () => {
      mql.removeEventListener('change', handleChange);
    };
  }, []);

  useEffect(() => {
    const el = backgroundRef.current;
    if (!el) return;
    if (mobileMenuOpen) {
      wasMobileMenuOpenRef.current = true;
      el.setAttribute('inert', '');
      el.setAttribute('aria-hidden', 'true');
    } else {
      el.removeAttribute('inert');
      el.removeAttribute('aria-hidden');
      if (wasMobileMenuOpenRef.current) {
        wasMobileMenuOpenRef.current = false;
        if (shouldRestoreFocusRef.current) {
          shouldRestoreFocusRef.current = false;
          mobileMenuButtonRef.current?.focus();
        }
      }
    }
    return () => {
      el.removeAttribute('inert');
      el.removeAttribute('aria-hidden');
    };
  }, [mobileMenuOpen]);

  function handleDismissMobile() {
    shouldRestoreFocusRef.current = true;
    setMobileMenuOpen(false);
  }

  function handleNavigateMobile() {
    shouldRestoreFocusRef.current = false;
    setMobileMenuOpen(false);
  }

  return (
    <div className="flex min-h-screen bg-[var(--color-canvas)]">
      <Sidebar
        mobileOpen={mobileMenuOpen}
        onCloseMobile={handleDismissMobile}
        onNavigateMobile={handleNavigateMobile}
      />
      <div ref={backgroundRef} className="flex min-w-0 flex-1 flex-col">
        <TopBar
          onOpenMobileMenu={() => {
            shouldRestoreFocusRef.current = true;
            setMobileMenuOpen(true);
          }}
          mobileMenuOpen={mobileMenuOpen}
          mobileMenuButtonRef={mobileMenuButtonRef}
        />
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
