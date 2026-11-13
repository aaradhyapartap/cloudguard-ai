import { RequireAuth } from '@/components/auth/RequireAuth';
import { WorkspaceShell } from '@/components/layout/WorkspaceShell';

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <WorkspaceShell>{children}</WorkspaceShell>
    </RequireAuth>
  );
}
