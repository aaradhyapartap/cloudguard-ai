'use client';

import { LogOut, Menu } from 'lucide-react';
import { useAuth } from '@/components/auth/AuthProvider';
import { HealthBadge } from '@/components/system/HealthBadge';

interface TopBarProps {
  onOpenMobileMenu?: () => void;
  mobileMenuOpen?: boolean;
  mobileMenuButtonRef?: React.RefObject<HTMLButtonElement | null>;
}

/**
 * Identity strip.
 *
 * Reads the caller from the auth context, which got it from `/me` — not by
 * decoding the token client-side.
 */
export function TopBar({
  onOpenMobileMenu,
  mobileMenuOpen = false,
  mobileMenuButtonRef,
}: TopBarProps) {
  const { me, logout } = useAuth();

  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-line)] bg-[var(--color-surface)] px-4 py-3 sm:px-6">
      <div className="flex items-center gap-3">
        {onOpenMobileMenu && (
          <button
            ref={mobileMenuButtonRef}
            type="button"
            onClick={onOpenMobileMenu}
            aria-label="Open navigation menu"
            aria-expanded={mobileMenuOpen}
            aria-haspopup="dialog"
            className="inline-flex items-center justify-center rounded-[var(--radius-sm)] border border-[var(--color-line)] p-1.5 text-[var(--color-ink-muted)] hover:bg-[var(--color-surface-sunken)] hover:text-[var(--color-ink)] md:hidden"
          >
            <Menu size={16} aria-hidden />
          </button>
        )}
        <HealthBadge />
      </div>

      <div className="flex items-center gap-3 sm:gap-4">
        {me && (
          <div className="flex items-center gap-2.5">
            <div className="text-right leading-tight">
              <p className="text-sm font-medium text-[var(--color-ink)]">{me.email}</p>
              <p className="font-mono text-[11px] text-[var(--color-ink-subtle)]">
                {me.role}
                {me.department ? ` · ${me.department}` : ''}
                {me.visible_confidentiality_levels.length > 0
                  ? ` · ${me.visible_confidentiality_levels[me.visible_confidentiality_levels.length - 1]}`
                  : ''}
              </p>
            </div>
            <div
              aria-hidden="true"
              className="flex size-8 items-center justify-center rounded-[var(--radius-md)] border border-[var(--color-accent-border)] bg-[var(--color-accent-soft)] font-mono text-xs font-semibold text-[var(--color-accent)]"
            >
              {me.email.slice(0, 2).toUpperCase()}
            </div>
          </div>
        )}

        <button
          type="button"
          onClick={logout}
          title="Sign out"
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-ink)] transition-colors hover:bg-[var(--color-surface-sunken)]"
        >
          <LogOut size={13} aria-hidden />
          <span>Sign out</span>
        </button>
      </div>
    </header>
  );
}
