'use client';

import { useEffect, useRef } from 'react';
import {
  BarChart3,
  FileText,
  LayoutDashboard,
  MessageSquare,
  Settings,
  ShieldAlert,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/components/auth/AuthProvider';

export interface NavItem {
  href: string;
  label: string;
  icon: typeof LayoutDashboard;
  anyPermissions?: readonly string[];
  allPermissions?: readonly string[];
}

export const WORKSPACE_NAV: readonly NavItem[] = [
  { href: '/dashboard', label: 'Overview', icon: LayoutDashboard },
  {
    href: '/documents',
    label: 'Documents',
    icon: FileText,
    allPermissions: ['document:read'],
  },
  {
    href: '/assistant',
    label: 'AI Assistant',
    icon: MessageSquare,
    allPermissions: ['document:read', 'ai:query'],
  },
  {
    href: '/risks',
    label: 'Risk & Approvals',
    icon: ShieldAlert,
    anyPermissions: ['risk:read', 'approval:read'],
  },
  {
    href: '/analytics',
    label: 'Analytics',
    icon: BarChart3,
    anyPermissions: ['analytics:read_own', 'analytics:read_team'],
  },
  {
    href: '/settings',
    label: 'Settings',
    icon: Settings,
    allPermissions: ['settings:manage'],
  },
];

function isNavItemVisible(item: NavItem, can: (permission: string) => boolean): boolean {
  if (item.allPermissions && !item.allPermissions.every((perm) => can(perm))) {
    return false;
  }
  if (item.anyPermissions && !item.anyPermissions.some((perm) => can(perm))) {
    return false;
  }
  return true;
}

interface SidebarProps {
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
  onNavigateMobile?: () => void;
  returnFocusRef?: React.RefObject<HTMLButtonElement | null>;
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Sidebar({
  mobileOpen = false,
  onCloseMobile,
  onNavigateMobile,
  returnFocusRef,
}: SidebarProps) {
  const pathname = usePathname();
  const { can, me } = useAuth();
  const drawerRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const wasOpenRef = useRef(false);

  const visible = WORKSPACE_NAV.filter((item) => isNavItemVisible(item, can));

  useEffect(() => {
    if (mobileOpen) {
      wasOpenRef.current = true;
      closeButtonRef.current?.focus();

      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key === 'Escape') {
          event.preventDefault();
          onCloseMobile?.();
          return;
        }
        if (event.key === 'Tab' && drawerRef.current) {
          const focusables = Array.from(
            drawerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR),
          );
          if (focusables.length === 0) return;
          const first = focusables[0]!;
          const last = focusables[focusables.length - 1]!;
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      };

      document.addEventListener('keydown', handleKeyDown);
      return () => {
        document.removeEventListener('keydown', handleKeyDown);
      };
    }

    if (wasOpenRef.current) {
      wasOpenRef.current = false;
      returnFocusRef?.current?.focus();
    }
  }, [mobileOpen, onCloseMobile, returnFocusRef]);

  function renderNavContent(includeCloseButton: boolean) {
    return (
      <div className="flex h-full flex-col bg-[var(--color-rail)] text-[var(--color-ink-inverse)]">
        <div className="flex items-center justify-between border-b border-[var(--color-rail-border)] px-5 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex size-7 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--color-accent)] font-mono text-xs font-semibold text-white">
              CG
            </div>
            <div>
              <p className="text-sm font-semibold tracking-tight text-white">
                CloudGuard AI
              </p>
              <p className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
                Compliance Workspace
              </p>
            </div>
          </div>

          {includeCloseButton && onCloseMobile && (
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onCloseMobile}
              aria-label="Close navigation menu"
              className="rounded-[var(--radius-sm)] p-1 text-slate-300 hover:bg-[var(--color-rail-hover)] hover:text-white md:hidden"
            >
              <X size={18} aria-hidden />
            </button>
          )}
        </div>

        <nav aria-label="Primary" className="flex-1 overflow-y-auto px-3 py-4">
          <ul className="space-y-1">
            {visible.map(({ href, label, icon: Icon }) => {
              const normalizedPath = pathname?.replace(/\/$/, '') || '/';
              const active = normalizedPath === href;
              return (
                <li key={href}>
                  <Link
                    href={href}
                    onClick={
                      includeCloseButton ? (onNavigateMobile ?? onCloseMobile) : undefined
                    }
                    aria-current={active ? 'page' : undefined}
                    className={`flex items-center gap-3 rounded-[var(--radius-md)] px-3 py-2.5 text-sm transition-colors ${
                      active
                        ? 'border-l-2 border-emerald-400 bg-[var(--color-rail-active)] font-medium text-white'
                        : 'text-slate-300 hover:bg-[var(--color-rail-hover)] hover:text-white'
                    }`}
                  >
                    <Icon
                      size={16}
                      aria-hidden
                      className={active ? 'text-emerald-300' : 'text-slate-400'}
                    />
                    <span>{label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="border-t border-[var(--color-rail-border)] px-5 py-3.5">
          <p className="font-mono text-[11px] text-slate-400">
            {me ? `Role: ${me.role}` : 'Unauthenticated'}
          </p>
          <p className="mt-0.5 font-mono text-[10px] text-slate-500">
            Tenant-isolated · RLS enforced
          </p>
        </div>
      </div>
    );
  }

  return (
    <>
      <aside className="hidden w-60 shrink-0 md:block">{renderNavContent(false)}</aside>

      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 flex md:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Workspace navigation"
        >
          <div
            className="fixed inset-0 bg-slate-950/60"
            onClick={onCloseMobile}
            aria-hidden="true"
          />
          <aside
            ref={drawerRef}
            className="relative z-50 w-64 max-w-[80vw] shrink-0 shadow-xl"
          >
            {renderNavContent(true)}
          </aside>
        </div>
      )}
    </>
  );
}
