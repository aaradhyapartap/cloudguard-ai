import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import DocumentsPage from './page';

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
    ) {
      super(message);
    }
  },
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

import { ApiError } from '@/lib/api-client';

const SAMPLE_DOC = {
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  filename: 'access-control-policy.pdf',
  content_type: 'application/pdf',
  size_bytes: 2048,
  document_type: 'policy',
  confidentiality_level: 'internal',
  processing_status: 'ready',
  processing_error: null,
  uploader_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  department: 'security',
  source: 'manual',
  tags: ['soc2'],
  created_at: '2026-11-12T00:00:00Z',
  updated_at: '2026-11-12T00:00:00Z',
};

describe('DocumentsPage', () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockPost.mockReset();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('labels memory:// registration limitation and handles missing-object 404 on completion', async () => {
    const perms = new Set(['document:read', 'document:upload']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    const queuedDoc = {
      ...SAMPLE_DOC,
      id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      filename: 'new-policy.pdf',
      processing_status: 'queued',
    };

    mockGet.mockImplementation((path: string) => {
      if (path.startsWith('/documents?')) {
        return Promise.resolve({
          items: [SAMPLE_DOC],
          total: 1,
          limit: 15,
          offset: 0,
        });
      }
      if (path.startsWith('/documents/')) {
        return Promise.resolve(SAMPLE_DOC);
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });

    mockPost.mockImplementation((path: string) => {
      if (path === '/documents') {
        return Promise.resolve({
          document: queuedDoc,
          upload_url: 'memory://documents/new-policy.pdf',
          expires_in_seconds: 900,
        });
      }
      if (path.endsWith('/complete')) {
        return Promise.reject(
          new ApiError(
            404,
            'NOT_FOUND',
            'The uploaded document object does not exist.',
          ),
        );
      }
      if (path === '/retrieval/search') {
        return Promise.resolve({
          total: 1,
          matches: [
            {
              chunk_id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
              document_id: SAMPLE_DOC.id,
              score: 0.912,
              content: 'Quarterly access reviews are mandatory.',
              metadata: {},
            },
          ],
        });
      }
      return Promise.reject(new Error(`Unexpected POST ${path}`));
    });

    render(<DocumentsPage />);

    await waitFor(() => {
      expect(screen.getAllByText('access-control-policy.pdf').length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByLabelText('Filename'), {
      target: { value: 'new-policy.pdf' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Register upload' }));

    await waitFor(() => {
      expect(screen.getByText('memory://documents/new-policy.pdf')).toBeTruthy();
      expect(screen.getByText(/Local registration-only mode/i)).toBeTruthy();
    });

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Verify completion (expects 404 without bytes)',
      }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(/The uploaded document object does not exist\. \(status 404\)/i),
      ).toBeTruthy();
    });
    // Document remains queued in metadata panel
    expect(screen.getAllByText('new-policy.pdf').length).toBeGreaterThan(0);
    expect(screen.getAllByText('queued').length).toBeGreaterThan(0);
  });

  it('uploads selected file bytes to presigned URL without Authorization header before calling complete', async () => {
    const perms = new Set(['document:read', 'document:upload']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(null, { status: 200 }));

    mockGet.mockResolvedValue({ items: [], total: 0, limit: 15, offset: 0 });
    mockPost.mockImplementation((path: string) => {
      if (path === '/documents') {
        return Promise.resolve({
          document: {
            ...SAMPLE_DOC,
            id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
            filename: 'evidence.txt',
            content_type: 'text/plain',
            size_bytes: 11,
            processing_status: 'queued',
          },
          upload_url: 'https://s3.example.test/presigned-put',
          expires_in_seconds: 900,
        });
      }
      if (path === '/documents/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/complete') {
        return Promise.resolve({
          ...SAMPLE_DOC,
          id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
          filename: 'evidence.txt',
          processing_status: 'queued',
        });
      }
      return Promise.reject(new Error(`Unexpected POST ${path}`));
    });

    render(<DocumentsPage />);

    const file = new File(['hello world'], 'evidence.txt', { type: 'text/plain' });
    fireEvent.change(
      screen.getByLabelText(/Select file \(recommended for presigned uploads\)/i),
      { target: { files: [file] } },
    );

    fireEvent.click(screen.getByRole('button', { name: 'Register upload' }));

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: 'Upload bytes & complete' }),
      ).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Upload bytes & complete' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        'https://s3.example.test/presigned-put',
        expect.objectContaining({
          method: 'PUT',
          headers: { 'Content-Type': 'text/plain' },
          body: file,
        }),
      );
      expect(mockPost).toHaveBeenCalledWith(
        '/documents/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/complete',
        {},
      );
    });
  });

  it('does not call complete when presigned PUT fails', async () => {
    const perms = new Set(['document:read', 'document:upload']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(null, { status: 403 }),
    );

    mockGet.mockResolvedValue({ items: [], total: 0, limit: 15, offset: 0 });
    mockPost.mockResolvedValueOnce({
      document: {
        ...SAMPLE_DOC,
        id: 'ffffffff-ffff-4fff-8fff-ffffffffffff',
        filename: 'fail.txt',
        processing_status: 'queued',
      },
      upload_url: 'https://s3.example.test/presigned-put',
      expires_in_seconds: 900,
    });

    render(<DocumentsPage />);

    const file = new File(['hello'], 'fail.txt', { type: 'text/plain' });
    fireEvent.change(
      screen.getByLabelText(/Select file \(recommended for presigned uploads\)/i),
      { target: { files: [file] } },
    );
    fireEvent.click(screen.getByRole('button', { name: 'Register upload' }));

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: 'Upload bytes & complete' }),
      ).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Upload bytes & complete' }));

    await waitFor(() => {
      expect(
        screen.getByText(/Presigned object upload failed with HTTP 403/i),
      ).toBeTruthy();
    });
    expect(mockPost).toHaveBeenCalledTimes(1);
  });

  it('hides upload form for Admin without document:upload', async () => {
    const perms = new Set(['document:read']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });
    mockGet.mockResolvedValue({ items: [], total: 0, limit: 15, offset: 0 });

    render(<DocumentsPage />);

    await waitFor(() => {
      expect(screen.getByText(/Read-only access/i)).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: 'Register upload' })).toBeNull();
  });

  it('binds registered file A to its upload so selecting file B later never PUTs B to A destination', async () => {
    const perms = new Set(['document:read', 'document:upload']);
    mockUseAuth.mockReturnValue({
      can: (p: string) => perms.has(p),
    });

    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(null, { status: 200 }));

    mockGet.mockResolvedValue({ items: [], total: 0, limit: 15, offset: 0 });
    mockPost
      .mockResolvedValueOnce({
        document: {
          ...SAMPLE_DOC,
          id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          filename: 'file-a.txt',
          size_bytes: 6,
          processing_status: 'queued',
        },
        upload_url: 'https://s3.example.test/presigned-put-a',
        expires_in_seconds: 900,
      })
      .mockResolvedValueOnce({
        ...SAMPLE_DOC,
        id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        filename: 'file-a.txt',
        size_bytes: 6,
        processing_status: 'queued',
      });

    render(<DocumentsPage />);

    const fileInput = screen.getByLabelText(
      /Select file \(recommended for presigned uploads\)/i,
    );
    const fileA = new File(['file-a'], 'file-a.txt', { type: 'text/plain' });
    const fileB = new File(['file-b-longer'], 'file-b.txt', { type: 'text/plain' });

    fireEvent.change(fileInput, { target: { files: [fileA] } });
    fireEvent.click(screen.getByRole('button', { name: 'Register upload' }));

    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: 'Upload bytes & complete' }),
      ).toBeTruthy();
      expect(screen.getByText(/Bound registration:/i)).toBeTruthy();
    });

    // User selects file B after registering file A
    fireEvent.change(fileInput, { target: { files: [fileB] } });

    fireEvent.click(screen.getByRole('button', { name: 'Upload bytes & complete' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        'https://s3.example.test/presigned-put-a',
        expect.objectContaining({
          method: 'PUT',
          headers: { 'Content-Type': 'text/plain' },
          body: fileA,
        }),
      );
      expect(mockPost).toHaveBeenCalledWith(
        '/documents/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/complete',
        {},
      );
    });
  });
});
