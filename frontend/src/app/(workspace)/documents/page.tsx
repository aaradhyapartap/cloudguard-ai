'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { FileText, Search, Upload } from 'lucide-react';
import { useAuth } from '@/components/auth/AuthProvider';
import { ApiError, api } from '@/lib/api-client';
import type {
  ConfidentialityLevel,
  DocumentCreateRequest,
  DocumentResponse,
  DocumentType,
  DocumentUploadResponse,
  Page,
  ProcessingStatus,
  RetrievalResponse,
} from '@/lib/types';

const DOCUMENT_TYPES: readonly DocumentType[] = [
  'policy',
  'security_policy',
  'audit_report',
  'control_documentation',
  'sop',
  'risk_report',
  'vendor_document',
  'contract',
  'financial_report',
  'invoice',
  'erp_export',
  'unknown',
];

const CONFIDENTIALITY_LEVELS: readonly ConfidentialityLevel[] = [
  'public',
  'internal',
  'confidential',
  'restricted',
];

const STATUS_BADGE: Record<ProcessingStatus, string> = {
  queued: 'border-slate-300 bg-slate-100 text-slate-700',
  extracting: 'border-amber-300 bg-amber-50 text-amber-800',
  indexing: 'border-amber-300 bg-amber-50 text-amber-800',
  ready:
    'border-[var(--color-accent-border)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]',
  failed: 'border-red-300 bg-red-50 text-[var(--color-error)]',
  quarantined: 'border-red-400 bg-red-100 text-red-900',
};

const PAGE_SIZE = 15;

function isHttpPresignedUrl(url: string): boolean {
  return url.startsWith('https://') || url.startsWith('http://');
}

export default function DocumentsPage() {
  const { can } = useAuth();
  const canRead = can('document:read');
  const canUpload = can('document:upload');

  const [pageData, setPageData] = useState<Page<DocumentResponse> | null>(null);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [searchFilter, setSearchFilter] = useState('');
  const [selectedDoc, setSelectedDoc] = useState<DocumentResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Upload registration state
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [filename, setFilename] = useState('');
  const [contentType, setContentType] = useState('application/pdf');
  const [sizeBytes, setSizeBytes] = useState('2048');
  const [docType, setDocType] = useState<DocumentType>('policy');
  const [confidentiality, setConfidentiality] = useState<ConfidentialityLevel>('internal');
  const [department, setDepartment] = useState('');
  const [source, setSource] = useState('');
  const [tagsInput, setTagsInput] = useState('');
  const [submittingUpload, setSubmittingUpload] = useState(false);
  const [pendingUpload, setPendingUpload] = useState<{
    registration: DocumentUploadResponse;
    file: File | null;
    boundFilename: string;
    boundContentType: string;
    boundSizeBytes: number;
  } | null>(null);
  const [completingUpload, setCompletingUpload] = useState(false);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  // Semantic retrieval state
  const [retrievalQuery, setRetrievalQuery] = useState('');
  const [retrievalResult, setRetrievalResult] = useState<RetrievalResponse | null>(null);
  const [searchingChunks, setSearchingChunks] = useState(false);
  const [retrievalError, setRetrievalError] = useState<string | null>(null);

  const loadDocuments = useCallback(
    async (nextOffset: number) => {
      if (!canRead) {
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const res = await api.get<Page<DocumentResponse>>(
          `/documents?limit=${PAGE_SIZE}&offset=${nextOffset}`,
        );
        setPageData(res);
        setOffset(nextOffset);
        setSelectedDoc((prev) => prev ?? res.items[0] ?? null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Failed to load documents.');
      } finally {
        setLoading(false);
      }
    },
    [canRead],
  );

  useEffect(() => {
    void loadDocuments(0);
  }, [loadDocuments]);

  const filteredItems = useMemo(() => {
    const items = pageData?.items ?? [];
    return items.filter((doc) => {
      if (statusFilter !== 'all' && doc.processing_status !== statusFilter) return false;
      if (searchFilter.trim()) {
        const q = searchFilter.trim().toLowerCase();
        const matchesName = doc.filename.toLowerCase().includes(q);
        const matchesSource = (doc.source ?? '').toLowerCase().includes(q);
        const matchesDept = (doc.department ?? '').toLowerCase().includes(q);
        if (!matchesName && !matchesSource && !matchesDept) return false;
      }
      return true;
    });
  }, [pageData, statusFilter, searchFilter]);

  async function inspectDocument(docId: string) {
    setError(null);
    try {
      const detail = await api.get<DocumentResponse>(`/documents/${docId}`);
      setSelectedDoc(detail);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Unable to fetch document details.');
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    if (submittingUpload || completingUpload) return;
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    if (file) {
      setFilename(file.name);
      setContentType(file.type || 'application/octet-stream');
      setSizeBytes(String(file.size));
    }
  }

  async function handleRegisterUpload(event: React.FormEvent) {
    event.preventDefault();
    if (!canUpload || submittingUpload || completingUpload) return;
    setSubmittingUpload(true);
    setError(null);
    setUploadMessage(null);

    const fileSnapshot = selectedFile;
    const parsedSize = fileSnapshot ? fileSnapshot.size : Number.parseInt(sizeBytes, 10);
    if (Number.isNaN(parsedSize) || parsedSize < 0) {
      setError('Size (bytes) must be a non-negative integer.');
      setSubmittingUpload(false);
      return;
    }

    const boundFilename = (fileSnapshot ? fileSnapshot.name : filename).trim();
    const boundContentType = (fileSnapshot
      ? fileSnapshot.type || 'application/octet-stream'
      : contentType
    ).trim();

    const payload: DocumentCreateRequest = {
      filename: boundFilename,
      content_type: boundContentType,
      size_bytes: parsedSize,
      document_type: docType,
      confidentiality_level: confidentiality,
      department: department.trim() || null,
      source: source.trim() || null,
      tags: tagsInput
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean),
    };

    try {
      const created = await api.post<DocumentUploadResponse>('/documents', payload);
      setPendingUpload({
        registration: created,
        file: fileSnapshot,
        boundFilename,
        boundContentType,
        boundSizeBytes: parsedSize,
      });
      setSelectedDoc(created.document);
      if (created.upload_url.startsWith('memory://')) {
        setUploadMessage(
          `Registered metadata for "${created.document.filename}" (${created.document.processing_status}). Local memory:// storage does not accept browser byte uploads, so ingestion cannot start.`,
        );
      } else {
        setUploadMessage(
          `Registered "${created.document.filename}" (${created.document.processing_status}). Upload file bytes to the presigned destination to complete.`,
        );
      }
      await loadDocuments(0);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Document registration failed.');
    } finally {
      setSubmittingUpload(false);
    }
  }

  async function handlePresignedUploadAndComplete(binding: {
    registration: DocumentUploadResponse;
    file: File | null;
    boundFilename: string;
    boundContentType: string;
    boundSizeBytes: number;
  }) {
    if (!canUpload || completingUpload || submittingUpload) return;
    if (!binding.file) {
      setError('No local file was bound to this registration. Select a file before registering.');
      return;
    }

    setCompletingUpload(true);
    setError(null);
    setUploadMessage(null);

    try {
      // Never send API bearer credentials to an external/presigned S3 URL.
      const putResponse = await fetch(binding.registration.upload_url, {
        method: 'PUT',
        headers: {
          'Content-Type': binding.boundContentType || 'application/octet-stream',
        },
        body: binding.file,
      });

      if (!putResponse.ok) {
        setError(
          `Presigned object upload failed with HTTP ${putResponse.status}. Completion was not called.`,
        );
        return;
      }

      const completed = await api.post<DocumentResponse>(
        `/documents/${binding.registration.document.id}/complete`,
        {},
      );
      setSelectedDoc(completed);
      setPendingUpload(null);
      if (selectedFile === binding.file) {
        setSelectedFile(null);
        setFilename('');
      }
      setUploadMessage(
        `Bytes uploaded and completion accepted for "${completed.filename}" (status: ${completed.processing_status}).`,
      );
      await loadDocuments(offset);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Object upload failed before completion; document remains queued.',
      );
    } finally {
      setCompletingUpload(false);
    }
  }

  async function handleVerifyCompletion(documentId: string) {
    if (!canUpload || completingUpload || submittingUpload) return;
    setCompletingUpload(true);
    setError(null);
    setUploadMessage(null);
    try {
      const updated = await api.post<DocumentResponse>(
        `/documents/${documentId}/complete`,
        {},
      );
      setSelectedDoc(updated);
      setPendingUpload(null);
      setUploadMessage(
        `Upload completed for "${updated.filename}". Current status: ${updated.processing_status}.`,
      );
      await loadDocuments(offset);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? `${err.message} (status ${err.status})`
          : 'Failed to complete document upload.',
      );
    } finally {
      setCompletingUpload(false);
    }
  }

  async function handleRetrievalSearch(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = retrievalQuery.trim();
    if (!trimmed || searchingChunks) return;
    setSearchingChunks(true);
    setRetrievalError(null);
    try {
      const res = await api.post<RetrievalResponse>('/retrieval/search', {
        query: trimmed,
        top_k: 8,
      });
      setRetrievalResult(res);
    } catch (err) {
      setRetrievalError(
        err instanceof ApiError ? err.message : 'Semantic chunk search failed.',
      );
    } finally {
      setSearchingChunks(false);
    }
  }

  if (!canRead) {
    return (
      <div className="mx-auto max-w-4xl">
        <div className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-6">
          <h1 className="text-lg font-semibold text-[var(--color-ink)]">Documents</h1>
          <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
            Your role does not include <span className="font-mono">document:read</span>{' '}
            permission.
          </p>
        </div>
      </div>
    );
  }

  const total = pageData?.total ?? 0;
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="border-b border-[var(--color-line)] pb-5">
        <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-accent)]">
          Evidence &amp; Policy Corpus
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
          Documents &amp; Semantic Search
        </h1>
        <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
          Browse tenant-scoped documents within your confidentiality ceiling, register
          new uploads, and search indexed chunks.
        </p>
      </header>

      {error && (
        <div
          role="alert"
          className="border-l-[3px] border-l-[var(--color-error)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-ink)] shadow-xs"
        >
          {error}
        </div>
      )}

      {uploadMessage && (
        <div
          role="status"
          className="border-l-[3px] border-l-[var(--color-ok)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-ink)] shadow-xs"
        >
          {uploadMessage}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <section
            aria-label="Document list"
            className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)]"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-line)] px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <label htmlFor="doc-search" className="sr-only">
                  Filter documents by filename, source, or department
                </label>
                <input
                  id="doc-search"
                  type="search"
                  value={searchFilter}
                  onChange={(e) => setSearchFilter(e.target.value)}
                  placeholder="Filter current page…"
                  className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-ink)]"
                />
                <label htmlFor="doc-status-filter" className="sr-only">
                  Filter by processing status
                </label>
                <select
                  id="doc-status-filter"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 py-1.5 text-xs text-[var(--color-ink)]"
                >
                  <option value="all">All statuses</option>
                  <option value="queued">queued</option>
                  <option value="extracting">extracting</option>
                  <option value="indexing">indexing</option>
                  <option value="ready">ready</option>
                  <option value="failed">failed</option>
                  <option value="quarantined">quarantined</option>
                </select>
              </div>
              <p className="numeric text-xs text-[var(--color-ink-muted)]">
                Total: {total}
              </p>
            </div>

            {loading ? (
              <p className="px-4 py-8 text-sm text-[var(--color-ink-muted)]">
                Loading documents…
              </p>
            ) : filteredItems.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-[var(--color-ink-muted)]">
                <FileText
                  size={20}
                  aria-hidden
                  className="mx-auto mb-2 text-[var(--color-ink-subtle)]"
                />
                No documents match the current filter.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="border-b border-[var(--color-line)] bg-[var(--color-surface-sunken)] text-[var(--color-ink-muted)]">
                    <tr>
                      <th className="px-4 py-2.5 font-medium">Filename</th>
                      <th className="px-4 py-2.5 font-medium">Type</th>
                      <th className="px-4 py-2.5 font-medium">Clearance</th>
                      <th className="px-4 py-2.5 font-medium">Status</th>
                      <th className="px-4 py-2.5 text-right font-medium">Size</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[var(--color-line)]">
                    {filteredItems.map((doc) => {
                      const isSelected = selectedDoc?.id === doc.id;
                      return (
                        <tr
                          key={doc.id}
                          className={
                            isSelected
                              ? 'bg-[var(--color-accent-soft)]'
                              : 'hover:bg-[var(--color-surface-sunken)]'
                          }
                        >
                          <td className="px-4 py-2.5">
                            <button
                              type="button"
                              onClick={() => void inspectDocument(doc.id)}
                              className="text-left font-medium text-[var(--color-ink)] hover:text-[var(--color-accent)] hover:underline"
                            >
                              {doc.filename}
                            </button>
                          </td>
                          <td className="px-4 py-2.5 font-mono text-[11px] text-[var(--color-ink-muted)]">
                            {doc.document_type}
                          </td>
                          <td className="px-4 py-2.5 font-mono text-[11px] text-[var(--color-ink-muted)]">
                            {doc.confidentiality_level}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className={`inline-block rounded-[var(--radius-sm)] border px-1.5 py-0.5 font-mono text-[10px] ${STATUS_BADGE[doc.processing_status]}`}
                            >
                              {doc.processing_status}
                            </span>
                          </td>
                          <td className="numeric px-4 py-2.5 text-right text-[var(--color-ink-muted)]">
                            {doc.size_bytes.toLocaleString()} B
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div className="flex items-center justify-between border-t border-[var(--color-line)] px-4 py-2.5 text-xs">
              <span className="numeric text-[var(--color-ink-subtle)]">
                Showing {total === 0 ? 0 : offset + 1}–
                {Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={!hasPrev || loading}
                  onClick={() => void loadDocuments(Math.max(0, offset - PAGE_SIZE))}
                  className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1 text-xs disabled:opacity-40"
                >
                  Previous
                </button>
                <button
                  type="button"
                  disabled={!hasNext || loading}
                  onClick={() => void loadDocuments(offset + PAGE_SIZE)}
                  className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1 text-xs disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          </section>

          <section
            aria-labelledby="semantic-search-heading"
            className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5"
          >
            <h2
              id="semantic-search-heading"
              className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink)]"
            >
              <Search size={15} aria-hidden className="text-[var(--color-accent)]" />
              <span>Semantic Chunk Search</span>
            </h2>
            <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
              Queries <span className="font-mono">/api/v1/retrieval/search</span> across
              indexed document chunks within your clearance.
            </p>

            <form onSubmit={(e) => void handleRetrievalSearch(e)} className="mt-3 flex gap-2">
              <label htmlFor="retrieval-query" className="sr-only">
                Semantic search query
              </label>
              <input
                id="retrieval-query"
                type="text"
                value={retrievalQuery}
                onChange={(e) => setRetrievalQuery(e.target.value)}
                placeholder="Search indexed chunks (e.g., access review, encryption policy)…"
                className="min-w-0 flex-1 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-3 py-2 text-sm text-[var(--color-ink)]"
              />
              <button
                type="submit"
                disabled={searchingChunks || !retrievalQuery.trim()}
                className="rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-4 py-2 text-xs font-medium text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
              >
                {searchingChunks ? 'Searching…' : 'Search'}
              </button>
            </form>

            {retrievalError && (
              <p role="alert" className="mt-3 text-xs text-[var(--color-error)]">
                {retrievalError}
              </p>
            )}

            {retrievalResult && (
              <div className="mt-4 space-y-2">
                <p className="font-mono text-[11px] text-[var(--color-ink-subtle)]">
                  Returned {retrievalResult.total} match(es)
                </p>
                {retrievalResult.matches.length === 0 ? (
                  <p className="text-xs text-[var(--color-ink-muted)]">
                    No matching chunks found in the active vector store.
                  </p>
                ) : (
                  <ul className="space-y-2">
                    {retrievalResult.matches.map((match) => (
                      <li
                        key={match.chunk_id}
                        className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] p-3 text-xs"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-[11px] text-[var(--color-ink-muted)]">
                          <span>doc: {match.document_id}</span>
                          <span>score: {match.score.toFixed(3)}</span>
                        </div>
                        <p className="mt-1.5 whitespace-pre-wrap text-[var(--color-ink)]">
                          {match.content}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        </div>

        <div className="space-y-4">
          <section
            aria-labelledby="document-detail-heading"
            className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5"
          >
            <h2
              id="document-detail-heading"
              className="text-sm font-semibold text-[var(--color-ink)]"
            >
              Document Metadata
            </h2>
            {selectedDoc ? (
              <dl className="mt-3 space-y-2 border-t border-[var(--color-line)] pt-3 text-xs">
                <div>
                  <dt className="text-[var(--color-ink-muted)]">Filename</dt>
                  <dd className="font-medium text-[var(--color-ink)]">
                    {selectedDoc.filename}
                  </dd>
                </div>
                <div>
                  <dt className="text-[var(--color-ink-muted)]">Document ID</dt>
                  <dd className="numeric break-all text-[11px] text-[var(--color-ink)]">
                    {selectedDoc.id}
                  </dd>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <dt className="text-[var(--color-ink-muted)]">Status</dt>
                    <dd className="mt-0.5 font-mono text-[11px]">
                      {selectedDoc.processing_status}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[var(--color-ink-muted)]">Clearance</dt>
                    <dd className="mt-0.5 font-mono text-[11px]">
                      {selectedDoc.confidentiality_level}
                    </dd>
                  </div>
                </div>
                {selectedDoc.processing_error && (
                  <div className="border-l-2 border-l-[var(--color-error)] bg-red-50 px-2.5 py-1.5 text-xs text-[var(--color-error)]">
                    {selectedDoc.processing_error}
                  </div>
                )}
                {selectedDoc.tags.length > 0 && (
                  <div>
                    <dt className="text-[var(--color-ink-muted)]">Tags</dt>
                    <dd className="mt-1 flex flex-wrap gap-1">
                      {selectedDoc.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded-[var(--radius-sm)] bg-[var(--color-surface-sunken)] px-1.5 py-0.5 font-mono text-[10px]"
                        >
                          {tag}
                        </span>
                      ))}
                    </dd>
                  </div>
                )}
              </dl>
            ) : (
              <p className="mt-3 text-xs text-[var(--color-ink-muted)]">
                Select a document row to inspect metadata.
              </p>
            )}
          </section>

          {canUpload ? (
            <section
              aria-labelledby="register-upload-heading"
              className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5"
            >
              <h2
                id="register-upload-heading"
                className="flex items-center gap-2 text-sm font-semibold text-[var(--color-ink)]"
              >
                <Upload size={15} aria-hidden className="text-[var(--color-accent)]" />
                <span>Register Document Upload</span>
              </h2>
              <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
                Select a file to register actual file metadata and receive an upload
                destination.
              </p>

              <form
                onSubmit={(e) => void handleRegisterUpload(e)}
                className="mt-4 space-y-3 text-xs"
              >
                <div>
                  <label htmlFor="upload-file" className="block font-medium">
                    Select file (recommended for presigned uploads)
                  </label>
                  <input
                    id="upload-file"
                    type="file"
                    disabled={submittingUpload || completingUpload}
                    onChange={handleFileChange}
                    className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2 py-1.5 text-[11px] disabled:opacity-50"
                  />
                </div>

                <div>
                  <label htmlFor="upload-filename" className="block font-medium">
                    Filename
                  </label>
                  <input
                    id="upload-filename"
                    type="text"
                    required
                    value={filename}
                    onChange={(e) => setFilename(e.target.value)}
                    placeholder="soc2-access-control-policy.pdf"
                    className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                  />
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label htmlFor="upload-content-type" className="block font-medium">
                      Content type
                    </label>
                    <input
                      id="upload-content-type"
                      type="text"
                      required
                      value={contentType}
                      onChange={(e) => setContentType(e.target.value)}
                      className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5 font-mono text-[11px]"
                    />
                  </div>
                  <div>
                    <label htmlFor="upload-size" className="block font-medium">
                      Size (bytes)
                    </label>
                    <input
                      id="upload-size"
                      type="number"
                      min={0}
                      required
                      value={sizeBytes}
                      onChange={(e) => setSizeBytes(e.target.value)}
                      className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5 font-mono text-[11px]"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label htmlFor="upload-doc-type" className="block font-medium">
                      Document type
                    </label>
                    <select
                      id="upload-doc-type"
                      value={docType}
                      onChange={(e) => setDocType(e.target.value as DocumentType)}
                      className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2 py-1.5"
                    >
                      {DOCUMENT_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="upload-clearance" className="block font-medium">
                      Confidentiality
                    </label>
                    <select
                      id="upload-clearance"
                      value={confidentiality}
                      onChange={(e) =>
                        setConfidentiality(e.target.value as ConfidentialityLevel)
                      }
                      className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2 py-1.5"
                    >
                      {CONFIDENTIALITY_LEVELS.map((lvl) => (
                        <option key={lvl} value={lvl}>
                          {lvl}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label htmlFor="upload-dept" className="block font-medium">
                      Department
                    </label>
                    <input
                      id="upload-dept"
                      type="text"
                      value={department}
                      onChange={(e) => setDepartment(e.target.value)}
                      placeholder="security"
                      className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                    />
                  </div>
                  <div>
                    <label htmlFor="upload-source" className="block font-medium">
                      Source
                    </label>
                    <input
                      id="upload-source"
                      type="text"
                      value={source}
                      onChange={(e) => setSource(e.target.value)}
                      placeholder="manual-upload"
                      className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                    />
                  </div>
                </div>

                <div>
                  <label htmlFor="upload-tags" className="block font-medium">
                    Tags (comma-separated)
                  </label>
                  <input
                    id="upload-tags"
                    type="text"
                    value={tagsInput}
                    onChange={(e) => setTagsInput(e.target.value)}
                    placeholder="soc2, access-control"
                    className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                  />
                </div>

                <button
                  type="submit"
                  disabled={submittingUpload || completingUpload || !filename.trim()}
                  className="w-full rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-3 py-2 text-xs font-medium text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
                >
                  {submittingUpload ? 'Registering…' : 'Register upload'}
                </button>
              </form>

              {pendingUpload && (
                <div className="mt-4 space-y-2 rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] p-3 text-xs">
                  <p className="font-medium text-[var(--color-ink)]">
                    Upload destination issued
                  </p>
                  <p className="text-[11px] text-[var(--color-ink-muted)]">
                    Bound registration:{' '}
                    <span className="font-mono text-[var(--color-ink)]">
                      {pendingUpload.boundFilename}
                    </span>{' '}
                    ({pendingUpload.boundSizeBytes} bytes)
                  </p>
                  <p className="break-all font-mono text-[11px] text-[var(--color-ink-muted)]">
                    {pendingUpload.registration.upload_url}
                  </p>

                  {isHttpPresignedUrl(pendingUpload.registration.upload_url) ? (
                    <>
                      <p className="text-[11px] text-[var(--color-ink-muted)]">
                        Upload bytes directly to the presigned destination (without API
                        credentials) before calling completion.
                      </p>
                      <button
                        type="button"
                        disabled={completingUpload || submittingUpload || !pendingUpload.file}
                        onClick={() =>
                          void handlePresignedUploadAndComplete(pendingUpload)
                        }
                        className="w-full rounded-[var(--radius-sm)] bg-[var(--color-rail)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-rail-hover)] disabled:opacity-50"
                      >
                        {completingUpload
                          ? 'Uploading & completing…'
                          : 'Upload bytes & complete'}
                      </button>
                    </>
                  ) : (
                    <>
                      <p className="text-[11px] text-[var(--color-ink-muted)]">
                        Local registration-only mode (
                        <span className="font-mono">DOCUMENT_STORE=memory</span>): no
                        object bytes can be uploaded to{' '}
                        <span className="font-mono">memory://</span> from the browser, so
                        ingestion cannot run. Calling completion without uploaded bytes
                        returns 404.
                      </p>
                      <button
                        type="button"
                        disabled={completingUpload || submittingUpload}
                        onClick={() =>
                          void handleVerifyCompletion(pendingUpload.registration.document.id)
                        }
                        className="w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink)] hover:bg-[var(--color-canvas)] disabled:opacity-50"
                      >
                        {completingUpload
                          ? 'Checking completion…'
                          : 'Verify completion (expects 404 without bytes)'}
                      </button>
                    </>
                  )}
                </div>
              )}
            </section>
          ) : (
            <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-4 text-xs text-[var(--color-ink-muted)]">
              Read-only access: your role does not include{' '}
              <span className="font-mono">document:upload</span>.
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
