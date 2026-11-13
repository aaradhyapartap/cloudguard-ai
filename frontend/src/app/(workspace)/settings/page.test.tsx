import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SettingsPage from './page';

const mockUseAuth = vi.fn();
const mockGet = vi.fn();

vi.mock('@/components/auth/AuthProvider', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly code: string,
      message: string,
    ) {
      super(message);
    }
  },
  api: {
    get: (...args: unknown[]) => mockGet(...args),
  },
}));

describe('SettingsPage', () => {
  beforeEach(() => {
    mockGet.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders /system/config adapters and flags for Admin', async () => {
    mockUseAuth.mockReturnValue({
      can: (p: string) => p === 'settings:manage',
    });
    mockGet.mockImplementation((path: string) => {
      if (path === '/system/config') {
        return Promise.resolve({
          environment: 'local',
          llm_provider: 'mock',
          vector_store: 'memory',
          document_store: 'memory',
          event_publisher: 'memory',
          chat_model: 'mock-chat-v1',
          embedding_model: 'mock-embed-v1',
          feature_flags: { rag_enabled: true },
        });
      }
      if (path === '/health/ready') {
        return Promise.resolve({
          status: 'ok',
          environment: 'local',
          version: '0.1.0',
          checked_at: '2026-11-12T00:00:00Z',
          dependencies: { database: 'ok' },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });

    render(<SettingsPage />);

    await waitFor(() => {
      expect(screen.getByText('mock-chat-v1')).toBeTruthy();
      expect(screen.getByText('rag_enabled')).toBeTruthy();
      expect(screen.getByText('enabled')).toBeTruthy();
    });
  });

  it('shows permission notice when settings:manage is absent', () => {
    mockUseAuth.mockReturnValue({
      can: () => false,
    });

    render(<SettingsPage />);

    expect(screen.getByText(/requires/i)).toBeTruthy();
    expect(mockGet).not.toHaveBeenCalled();
  });
});
