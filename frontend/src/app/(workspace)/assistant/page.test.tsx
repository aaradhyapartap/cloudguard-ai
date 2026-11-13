import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AssistantPage from './page';

const mockUseAuth = vi.fn();
const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock('@/components/auth/AuthProvider', () => ({
  useAuth: () => mockUseAuth(),
}));

vi.mock('@/lib/api-client', () => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly code: string,
      message: string,
      readonly requestId?: string | null,
    ) {
      super(message);
    }
  },
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('AssistantPage', () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('submits a RAG query and renders answer, question, citations, and complete usage', async () => {
    const perms = new Set(['document:read', 'ai:query']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });
    mockGet.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    mockPost.mockResolvedValue({
      answer: 'Access reviews occur quarterly [S1].',
      sources: [
        {
          label: 'S1',
          chunk_id: '11111111-1111-4111-8111-111111111111',
          document_id: '22222222-2222-4222-8222-222222222222',
          score: 0.945,
          metadata: {},
        },
      ],
      retrieval_count: 1,
      model_id: 'mock-llm-v1',
      usage: { input_tokens: 120, output_tokens: 24 },
    });

    render(<AssistantPage />);

    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'How often are access reviews required?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Submit query' }));

    await waitFor(() => {
      expect(
        screen.getAllByText('How often are access reviews required?').length,
      ).toBe(2);
      expect(screen.getByText('Access reviews occur quarterly [S1].')).toBeTruthy();
      expect(screen.getByText('[S1]')).toBeTruthy();
      expect(screen.getByText('tokens: 120 in / 24 out')).toBeTruthy();
    });
  });

  it('handles cancel-then-resubmit and ignores stale out-of-order responses', async () => {
    const perms = new Set(['document:read', 'ai:query']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });
    mockGet.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });

    const first = deferred<unknown>();
    const second = deferred<unknown>();
    mockPost
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);

    render(<AssistantPage />);

    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'First stale question' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Submit query' }));

    // Cancel first request
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByText('Request cancelled.')).toBeTruthy();

    // Resubmit second request
    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'Second active question' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Submit query' }));

    // Second resolves first
    second.resolve({
      answer: 'Second answer wins.',
      sources: [],
      retrieval_count: 0,
      model_id: 'mock-llm-v1',
      usage: null,
    });

    await waitFor(() => {
      expect(screen.getAllByText('Second active question').length).toBe(2);
      expect(screen.getByText('Second answer wins.')).toBeTruthy();
      expect(screen.getByText('tokens: unavailable')).toBeTruthy();
    });

    // Late resolution of cancelled first request must NOT overwrite second
    first.resolve({
      answer: 'Stale first answer should be ignored.',
      sources: [],
      retrieval_count: 0,
      model_id: 'mock-llm-v1',
      usage: { input_tokens: 99, output_tokens: 99 },
    });

    await new Promise((r) => setTimeout(r, 20));
    expect(screen.getByText('Second answer wins.')).toBeTruthy();
    expect(screen.queryByText('Stale first answer should be ignored.')).toBeNull();
  });

  it('renders absent, partial, and zero token usage accurately', async () => {
    const perms = new Set(['document:read', 'ai:query']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });
    mockGet.mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });

    // Partial usage
    mockPost.mockResolvedValueOnce({
      answer: 'Partial usage answer.',
      sources: [],
      retrieval_count: 0,
      model_id: 'bedrock:claude',
      usage: { input_tokens: 10, output_tokens: null },
    });

    render(<AssistantPage />);
    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'Q1' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Submit query' }));

    await waitFor(() => {
      expect(screen.getByText('tokens: 10 in / unknown out')).toBeTruthy();
    });

    // Zero usage (must preserve 0 as 0)
    mockPost.mockResolvedValueOnce({
      answer: 'Zero usage answer.',
      sources: [],
      retrieval_count: 0,
      model_id: 'mock:chat-v1',
      usage: { input_tokens: 0, output_tokens: 0 },
    });

    fireEvent.change(screen.getByLabelText('Question'), {
      target: { value: 'Q2' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Submit query' }));

    await waitFor(() => {
      expect(screen.getByText('tokens: 0 in / 0 out')).toBeTruthy();
    });
  });

  it('denies access when ai:query permission is missing', () => {
    const perms = new Set(['document:read']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    render(<AssistantPage />);

    expect(screen.getByText(/require both/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Submit query' })).toBeNull();
  });
});
