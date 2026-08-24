import { RequireAuth } from '@/components/auth/RequireAuth';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';

/**
 * Workspace shell.
 *
 * A route group `(workspace)` rather than a path segment, so the URL stays
 * `/dashboard` instead of `/workspace/dashboard`.
 *
 * `RequireAuth` sits here rather than in each page, so every route beneath it
 * inherits the redirect. Note this is UX, not access control — the server
 * re-authorises every request regardless of what the browser chose to render.
 */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <div className="flex min-h-screen">
        <Sidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar />
          <main className="flex-1 px-6 py-6">{children}</main>
        </div>
      </div>
    </RequireAuth>
  );
}
