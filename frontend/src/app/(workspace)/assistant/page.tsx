'use client';

import { useEffect, useRef, useState } from 'react';
import { BookOpen, Info, MessageSquare, Send, XCircle } from 'lucide-react';
import { useAuth } from '@/components/auth/AuthProvider';
import { ApiError, api } from '@/lib/api-client';
import type { DocumentResponse, Page, RAGResponse, TokenUsage } from '@/lib/types';

function formatTokenCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'unknown';
  return String(value);
}

function formatUsageSummary(usage: TokenUsage | null): string {
  if (!usage) return 'tokens: unavailable';
  const input = formatTokenCount(usage.input_tokens);
  const output = formatTokenCount(usage.output_tokens);
  return `tokens: ${input} in / ${output} out`;
}

export default function AssistantPage() {
  const { can } = useAuth();
  const canQuery = can('document:read') && can('ai:query');

  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState(10);
  const [documents, setDocuments] = useState<DocumentResponse[]>([]);
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [responseState, setResponseState] = useState<{
    question: string;
    result: RAGResponse;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const activeRequestIdRef = useRef(0);

  useEffect(() => {
    if (!canQuery) return;
    let cancelled = false;
    async function loadDocs() {
      try {
        const res = await api.get<Page<DocumentResponse>>('/documents?limit=50&offset=0');
        if (!cancelled) setDocuments(res.items);
      } catch {
        // Optional document scope list; assistant remains usable without it.
      }
    }
    void loadDocs();
    return () => {
      cancelled = true;
    };
  }, [canQuery]);

  useEffect(() => {
    return () => {
      activeRequestIdRef.current += 1;
      abortRef.current?.abort();
    };
  }, []);

  function toggleDoc(docId: string) {
    setSelectedDocIds((prev) =>
      prev.includes(docId) ? prev.filter((id) => id !== docId) : [...prev, docId],
    );
  }

  function handleCancel() {
    activeRequestIdRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    setError('Request cancelled.');
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed || !canQuery) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const requestId = ++activeRequestIdRef.current;

    setLoading(true);
    setError(null);

    try {
      const payload = {
        query: trimmed,
        top_k: topK,
        document_ids: selectedDocIds.length > 0 ? selectedDocIds : null,
      };
      const result = await api.post<RAGResponse>('/rag/query', payload, {
        signal: controller.signal,
      });
      if (activeRequestIdRef.current !== requestId) return;
      setResponseState({ question: trimmed, result });
    } catch (err) {
      if (activeRequestIdRef.current !== requestId) return;
      if (err instanceof DOMException && err.name === 'AbortError') {
        setError('Request cancelled.');
      } else if (err instanceof ApiError) {
        if (err.status === 429) {
          setError(`Rate limit reached (${err.code}): ${err.message}`);
        } else {
          setError(`${err.message}${err.requestId ? ` (request: ${err.requestId})` : ''}`);
        }
      } else {
        setError('Unable to reach the RAG assistant endpoint.');
      }
    } finally {
      if (activeRequestIdRef.current === requestId) {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
        setLoading(false);
      }
    }
  }

  if (!canQuery) {
    return (
      <div className="mx-auto max-w-4xl">
        <div className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-6">
          <h1 className="text-lg font-semibold text-[var(--color-ink)]">AI Assistant</h1>
          <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
            Grounded AI queries require both{' '}
            <span className="font-mono">document:read</span> and{' '}
            <span className="font-mono">ai:query</span> permissions.
          </p>
        </div>
      </div>
    );
  }

  const response = responseState?.result ?? null;
  const isMockModel =
    response?.model_id.toLowerCase().includes('mock') ||
    response?.model_id.toLowerCase().includes('local');

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="border-b border-[var(--color-line)] pb-5">
        <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-accent)]">
          Grounded Retrieval-Augmented Generation
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
          Compliance AI Assistant
        </h1>
        <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
          Ask natural-language compliance questions grounded strictly in tenant document
          chunks within your confidentiality clearance.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-3">
        <section
          aria-labelledby="rag-form-heading"
          className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5 lg:col-span-2"
        >
          <h2 id="rag-form-heading" className="text-sm font-semibold text-[var(--color-ink)]">
            Ask a Grounded Question
          </h2>

          <form onSubmit={(e) => void handleSubmit(e)} className="mt-3 space-y-4">
            <div>
              <label htmlFor="rag-query" className="block text-xs font-medium">
                Question
              </label>
              <textarea
                id="rag-query"
                rows={4}
                required
                maxLength={2000}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Example: What controls govern privileged access reviews and encryption key rotation?"
                className="mt-1.5 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] p-3 text-sm text-[var(--color-ink)]"
              />
            </div>

            <div className="flex flex-wrap items-end justify-between gap-4">
              <div>
                <label htmlFor="rag-top-k" className="block text-xs font-medium">
                  Chunks to retrieve (top_k)
                </label>
                <input
                  id="rag-top-k"
                  type="number"
                  min={1}
                  max={100}
                  value={topK}
                  onChange={(e) =>
                    setTopK(Math.max(1, Math.min(100, Number(e.target.value) || 10)))
                  }
                  className="numeric mt-1 w-24 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5 text-xs"
                />
              </div>

              <div className="flex items-center gap-2">
                {loading && (
                  <button
                    type="button"
                    onClick={handleCancel}
                    className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-3 py-2 text-xs font-medium text-[var(--color-ink)] hover:bg-[var(--color-surface-sunken)]"
                  >
                    <XCircle size={14} aria-hidden />
                    <span>Cancel</span>
                  </button>
                )}
                <button
                  type="submit"
                  disabled={loading || !query.trim()}
                  className="inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-4 py-2 text-xs font-medium text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
                >
                  <Send size={14} aria-hidden />
                  <span>{loading ? 'Generating…' : 'Submit query'}</span>
                </button>
              </div>
            </div>
          </form>

          {error && (
            <p
              role="alert"
              className="mt-4 border-l-[3px] border-l-[var(--color-error)] bg-[var(--color-surface-sunken)] px-3 py-2 text-sm text-[var(--color-ink)]"
            >
              {error}
            </p>
          )}

          {response && responseState && (
            <div className="mt-6 space-y-4 border-t border-[var(--color-line)] pt-5">
              {isMockModel && (
                <div className="flex items-start gap-2 rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] px-3 py-2 text-xs text-[var(--color-ink-muted)]">
                  <Info size={14} className="mt-0.5 shrink-0 text-[var(--color-accent)]" aria-hidden />
                  <span>
                    Mock/local LLM provider responded (<span className="font-mono">{response.model_id}</span>).
                  </span>
                </div>
              )}

              <div className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] px-3 py-2 text-xs text-[var(--color-ink-muted)]">
                <span className="font-medium text-[var(--color-ink)]">Question asked: </span>
                <span>{responseState.question}</span>
              </div>

              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-ink)]">
                    <MessageSquare size={14} aria-hidden className="text-[var(--color-accent)]" />
                    <span>Answer</span>
                  </h3>
                  <div className="flex flex-wrap gap-3 font-mono text-[11px] text-[var(--color-ink-subtle)]">
                    <span>model: {response.model_id}</span>
                    <span>retrieved: {response.retrieval_count}</span>
                    <span>{formatUsageSummary(response.usage)}</span>
                  </div>
                </div>
                <div className="mt-2 whitespace-pre-wrap rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-canvas)] p-4 text-sm leading-relaxed text-[var(--color-ink)]">
                  {response.answer}
                </div>
              </div>

              <div>
                <h3 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-ink)]">
                  <BookOpen size={14} aria-hidden className="text-[var(--color-accent)]" />
                  <span>Citations ({response.sources.length})</span>
                </h3>
                {response.sources.length === 0 ? (
                  <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
                    No source citations returned for this query.
                  </p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {response.sources.map((src) => (
                      <li
                        key={`${src.label}-${src.chunk_id}`}
                        className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] p-3 text-xs"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2 font-mono text-[11px]">
                          <span className="font-semibold text-[var(--color-accent)]">
                            [{src.label}]
                          </span>
                          <span className="text-[var(--color-ink-muted)]">
                            score: {src.score.toFixed(3)}
                          </span>
                        </div>
                        <p className="mt-1 font-mono text-[11px] text-[var(--color-ink)]">
                          document: {src.document_id}
                        </p>
                        <p className="font-mono text-[11px] text-[var(--color-ink-muted)]">
                          chunk: {src.chunk_id}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </section>

        <aside
          aria-labelledby="scope-docs-heading"
          className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5"
        >
          <h2 id="scope-docs-heading" className="text-sm font-semibold text-[var(--color-ink)]">
            Optional Document Scope
          </h2>
          <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
            Leave unchecked to search all accessible tenant documents, or select specific
            documents to constrain retrieval.
          </p>

          {documents.length === 0 ? (
            <p className="mt-4 text-xs text-[var(--color-ink-muted)]">
              No documents listed yet.
            </p>
          ) : (
            <ul className="mt-3 max-h-80 space-y-1.5 overflow-y-auto border-t border-[var(--color-line)] pt-3">
              {documents.map((doc) => (
                <li key={doc.id}>
                  <label className="flex cursor-pointer items-start gap-2 rounded-[var(--radius-sm)] p-1.5 text-xs hover:bg-[var(--color-surface-sunken)]">
                    <input
                      type="checkbox"
                      checked={selectedDocIds.includes(doc.id)}
                      onChange={() => toggleDoc(doc.id)}
                      className="mt-0.5"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-[var(--color-ink)]">
                        {doc.filename}
                      </span>
                      <span className="block font-mono text-[10px] text-[var(--color-ink-subtle)]">
                        {doc.confidentiality_level} · {doc.processing_status}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  );
}
