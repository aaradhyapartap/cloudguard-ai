import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from './Sidebar';
import { WorkspaceShell } from './WorkspaceShell';

const mockUseAuth = vi.fn();

vi.mock('@/components/auth/AuthProvider', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
}));

vi.mock('@/components/system/HealthBadge', () => ({
  HealthBadge: () => <span>API connected</span>,
}));

describe('Sidebar & WorkspaceShell', () => {
  afterEach(() => {
    cleanup();
  });

  it('shows Analyst permitted routes and omits Settings and phase scaffolds', () => {
    const perms = new Set([
      'document:read',
      'document:upload',
      'ai:query',
      'risk:read',
      'compliance:create',
      'analytics:read_own',
    ]);
    mockUseAuth.mockReturnValue({
      me: { role: 'analyst', email: 'analyst@acme.test', visible_confidentiality_levels: ['internal'] },
      can: (p: string) => perms.has(p),
      logout: vi.fn(),
    });

    render(<Sidebar />);

    expect(screen.getByRole('link', { name: 'Overview' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Documents' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'AI Assistant' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Risk & Approvals' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Analytics' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Settings' })).toBeNull();
    expect(screen.queryByText(/Phase/i)).toBeNull();
  });

  it('shows Settings for Admin and omits AI Assistant when ai:query is absent', () => {
    const perms = new Set([
      'document:read',
      'risk:read',
      'approval:read',
      'analytics:read_team',
      'settings:manage',
    ]);
    mockUseAuth.mockReturnValue({
      me: { role: 'admin', email: 'admin@acme.test', visible_confidentiality_levels: ['restricted'] },
      can: (p: string) => perms.has(p),
      logout: vi.fn(),
    });

    render(<Sidebar />);

    expect(screen.getByRole('link', { name: 'Settings' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'AI Assistant' })).toBeNull();
  });

  it('supports accessible mobile drawer Escape dismissal, focus restoration, and background inert', () => {
    const perms = new Set(['document:read']);
    mockUseAuth.mockReturnValue({
      me: { role: 'analyst', email: 'analyst@acme.test', visible_confidentiality_levels: ['internal'] },
      can: (p: string) => perms.has(p),
      logout: vi.fn(),
    });

    render(
      <WorkspaceShell>
        <div>Main content</div>
      </WorkspaceShell>,
    );

    const openBtn = screen.getByRole('button', { name: 'Open navigation menu' });
    fireEvent.click(openBtn);

    const dialog = screen.getByRole('dialog', { name: 'Workspace navigation' });
    expect(dialog).toBeTruthy();
    const closeBtn = screen.getByRole('button', { name: 'Close navigation menu' });
    expect(document.activeElement).toBe(closeBtn);

    const mainEl = screen.getByText('Main content').closest('div[inert]');
    expect(mainEl).toBeTruthy();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Workspace navigation' })).toBeNull();
    expect(document.activeElement).toBe(openBtn);
  });

  it('traps Tab and Shift+Tab focus inside the mobile drawer', () => {
    const perms = new Set(['document:read', 'ai:query']);
    mockUseAuth.mockReturnValue({
      me: { role: 'analyst', email: 'analyst@acme.test', visible_confidentiality_levels: ['internal'] },
      can: (p: string) => perms.has(p),
      logout: vi.fn(),
    });

    render(
      <WorkspaceShell>
        <div>Main content</div>
      </WorkspaceShell>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));

    const dialog = screen.getByRole('dialog', { name: 'Workspace navigation' });
    const focusables = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    expect(focusables.length).toBeGreaterThan(1);
    const first = focusables[0]!;
    const last = focusables[focusables.length - 1]!;

    first.focus();
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(last);

    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(first);
  });

  it('closes mobile drawer and clears background inert when resizing to desktop breakpoint', () => {
    const listeners = new Set<(event: MediaQueryListEvent) => void>();
    let desktopMatches = false;
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query === '(min-width: 768px)' ? desktopMatches : false,
      media: query,
      onchange: null,
      addEventListener: (_type: string, cb: (event: MediaQueryListEvent) => void) => {
        listeners.add(cb);
      },
      removeEventListener: (_type: string, cb: (event: MediaQueryListEvent) => void) => {
        listeners.delete(cb);
      },
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));

    try {
      const perms = new Set(['document:read']);
      mockUseAuth.mockReturnValue({
        me: { role: 'analyst', email: 'analyst@acme.test', visible_confidentiality_levels: ['internal'] },
        can: (p: string) => perms.has(p),
        logout: vi.fn(),
      });

      render(
        <WorkspaceShell>
          <div>Main content</div>
        </WorkspaceShell>,
      );

      fireEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));
      expect(screen.getByRole('dialog', { name: 'Workspace navigation' })).toBeTruthy();
      expect(screen.getByText('Main content').closest('div[inert]')).toBeTruthy();

      desktopMatches = true;
      act(() => {
        for (const cb of listeners) {
          cb({ matches: true, media: '(min-width: 768px)' } as MediaQueryListEvent);
        }
      });

      expect(screen.queryByRole('dialog', { name: 'Workspace navigation' })).toBeNull();
      expect(screen.getByText('Main content').closest('div[inert]')).toBeNull();
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });
});
