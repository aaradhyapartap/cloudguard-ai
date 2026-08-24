'use client';

import {
  BarChart3,
  FileText,
  LayoutDashboard,
  MessageSquare,
  ScrollText,
  Search,
  Settings,
  ShieldAlert,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useAuth } from '@/components/auth/AuthProvider';

/**
 * Navigation rail.
 *
 * Every destination is listed, including the ones that arrive in later phases.
 * Those are marked disabled with the phase that delivers them rather than being
 * hidden — during a build this is a visible roadmap, and it stops the shell
 * from silently drifting away from the plan.
 *
 * Entries are filtered by the caller's permissions as reported by `/me`. The
 * permission strings come from the server's matrix, so navigation is derived
 * from the same rules that enforce access rather than from a second copy living
 * in the client that can drift out of step.
 *
 * Hiding a link is presentation only. The server returns 403 to anyone who asks
 * for a resource they may not have, whether or not the UI offered them a way to
 * ask.
 */
const NAV = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, ready: true },
  {
    href: '/documents',
    label: 'Documents',
    icon: FileText,
    phase: 'Phase 3',
    permission: 'document:read',
  },
  {
    href: '/assistant',
    label: 'AI Assistant',
    icon: MessageSquare,
    phase: 'Phase 4',
    permission: 'ai:query',
  },
  {
    href: '/risks',
    label: 'Risk Center',
    icon: ShieldAlert,
    phase: 'Phase 5',
    permission: 'risk:read',
  },
  {
    href: '/investigations',
    label: 'Investigations',
    icon: Search,
    phase: 'Phase 6',
    permission: 'investigation:read',
  },
  {
    href: '/analytics',
    label: 'Analytics',
    icon: BarChart3,
    phase: 'Phase 9',
    permission: 'analytics:read_own',
  },
  {
    href: '/audit',
    label: 'Audit Log',
    icon: ScrollText,
    phase: 'Phase 7',
    permission: 'audit:read',
  },
  {
    href: '/settings',
    label: 'Settings',
    icon: Settings,
    phase: 'Phase 2',
    permission: 'settings:manage',
  },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const { can } = useAuth();
  const visible = NAV.filter(
    (item) => !('permission' in item) || can(item.permission),
  );

  return (
    <nav
      aria-label="Primary"
      className="flex w-56 shrink-0 flex-col bg-[var(--color-rail)] text-[var(--color-ink-inverse)]"
    >
      <div className="border-b border-white/10 px-4 py-4">
        <p className="text-sm font-semibold tracking-tight">CloudGuard</p>
        <p className="mt-0.5 font-mono text-[11px] text-white/45">
          compliance intelligence
        </p>
      </div>

      <ul className="flex-1 space-y-0.5 p-2">
        {visible.map(({ href, label, icon: Icon, ...rest }) => {
          const ready = 'ready' in rest && rest.ready;
          const active = pathname === href;

          if (!ready) {
            return (
              <li key={href}>
                <span
                  aria-disabled="true"
                  title={`Arrives in ${'phase' in rest ? rest.phase : 'a later phase'}`}
                  className="flex cursor-not-allowed items-center gap-2.5 rounded-[var(--radius-md)] px-2.5 py-2 text-sm text-white/30"
                >
                  <Icon size={15} aria-hidden />
                  <span className="flex-1">{label}</span>
                  <span className="font-mono text-[10px] text-white/25">
                    {'phase' in rest ? rest.phase.replace('Phase ', 'P') : ''}
                  </span>
                </span>
              </li>
            );
          }

          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={active ? 'page' : undefined}
                className={`flex items-center gap-2.5 rounded-[var(--radius-md)] px-2.5 py-2 text-sm transition-colors ${
                  active
                    ? 'bg-[var(--color-rail-active)] font-medium text-white'
                    : 'text-white/70 hover:bg-[var(--color-rail-hover)] hover:text-white'
                }`}
              >
                <Icon size={15} aria-hidden />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>

      <p className="border-t border-white/10 px-4 py-3 font-mono text-[10px] text-white/35">
        v0.1.0 · phase 1
      </p>
    </nav>
  );
}
