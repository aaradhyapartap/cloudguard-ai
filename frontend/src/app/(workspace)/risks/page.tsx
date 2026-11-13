'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { CheckCircle2, ClipboardCheck, ShieldAlert, Sparkles } from 'lucide-react';
import { useAuth } from '@/components/auth/AuthProvider';
import { ApiError, api } from '@/lib/api-client';
import type {
  ApprovalDecision,
  ApprovalDecisionRequest,
  AssessmentScoreResult,
  AssessmentScoreSnapshotProjection,
  ComplianceAssessmentProjection,
  ComplianceAssessmentResponse,
  ComplianceCandidateExtractionResult,
  ControlAssessmentProjection,
  ControlStatus,
  DecidedApproval,
  PendingApproval,
  RiskClassification,
  ScoreOverrideResponse,
} from '@/lib/types';

const CONTROL_STATUSES: readonly ControlStatus[] = [
  'unassessed',
  'satisfied',
  'partially_satisfied',
  'deficient',
  'not_applicable',
];

const SEVERITY_RULE: Record<RiskClassification, string> = {
  not_scored: 'border-l-slate-300',
  low: 'border-l-[var(--color-severity-low)]',
  medium: 'border-l-[var(--color-severity-medium)]',
  high: 'border-l-[var(--color-severity-high)]',
  critical: 'border-l-[var(--color-severity-critical)]',
};

export default function RisksPage() {
  const { can } = useAuth();
  const canReadRisks = can('risk:read');
  const canCreateCompliance = can('compliance:create');
  const canReviewRisk = can('risk:review');
  const canOverrideRisk = can('risk:modify_severity');
  const canReadApprovals = can('approval:read');
  const canDecideApprovals = can('approval:decide');

  const [activeTab, setActiveTab] = useState<'assessments' | 'approvals'>(
    canReadRisks ? 'assessments' : 'approvals',
  );
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // Compliance Assessments state
  const [assessments, setAssessments] = useState<ComplianceAssessmentResponse[]>([]);
  const [selectedAssessment, setSelectedAssessment] =
    useState<ComplianceAssessmentProjection | null>(null);
  const [snapshots, setSnapshots] = useState<AssessmentScoreSnapshotProjection[]>([]);
  const [overrides, setOverrides] = useState<ScoreOverrideResponse[]>([]);
  const [candidates, setCandidates] =
    useState<ComplianceCandidateExtractionResult | null>(null);
  const [loadingAssessments, setLoadingAssessments] = useState(false);

  // Create assessment form
  const [newFrameworkId, setNewFrameworkId] = useState('');
  const [newTitle, setNewTitle] = useState('');
  const [creatingAssessment, setCreatingAssessment] = useState(false);

  // Selected control editing
  const [editingControlId, setEditingControlId] = useState<string | null>(null);
  const [controlStatusDraft, setControlStatusDraft] =
    useState<ControlStatus>('unassessed');
  const [controlWeightDraft, setControlWeightDraft] = useState('1.0');
  const [controlRationaleDraft, setControlRationaleDraft] = useState('');
  const [savingControl, setSavingControl] = useState(false);

  // Evidence admission
  const [evidenceDocId, setEvidenceDocId] = useState('');
  const [evidenceChunkId, setEvidenceChunkId] = useState('');
  const [admittingEvidence, setAdmittingEvidence] = useState(false);

  // Manager override form
  const [overrideScore, setOverrideScore] = useState('80.00');
  const [overrideJustification, setOverrideJustification] = useState('');
  const [submittingOverride, setSubmittingOverride] = useState(false);
  const [runningAction, setRunningAction] = useState(false);

  // Approvals state
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
  const [selectedApproval, setSelectedApproval] = useState<
    PendingApproval | DecidedApproval | null
  >(null);
  const [loadingApprovals, setLoadingApprovals] = useState(false);
  const [decisionChoice, setDecisionChoice] = useState<ApprovalDecision>('approved');
  const [decisionJustification, setDecisionJustification] = useState('');
  const [decisionComment, setDecisionComment] = useState('');
  const [modActionType, setModActionType] = useState('');
  const [modResourceType, setModResourceType] = useState('');
  const [modResourceId, setModResourceId] = useState('');
  const [modSummary, setModSummary] = useState('');
  const [submittingDecision, setSubmittingDecision] = useState(false);
  const [loadingApprovalDetail, setLoadingApprovalDetail] = useState(false);
  const [requestedApprovalId, setRequestedApprovalId] = useState<string | null>(null);
  const [approvalDetailError, setApprovalDetailError] = useState<string | null>(null);
  const approvalRequestSeqRef = useRef(0);
  const requestedApprovalIdRef = useRef<string | null>(null);
  const decisionSeqRef = useRef(0);

  const loadAssessmentDetail = useCallback(async (assessmentId: string) => {
    setError(null);
    setCandidates(null);
    try {
      const [projection, snapList, overrideList] = await Promise.all([
        api.get<ComplianceAssessmentProjection>(
          `/compliance/assessments/${assessmentId}`,
        ),
        api.get<AssessmentScoreSnapshotProjection[]>(
          `/compliance/assessments/${assessmentId}/snapshots`,
        ),
        api.get<ScoreOverrideResponse[]>(
          `/compliance/assessments/${assessmentId}/overrides`,
        ),
      ]);
      setSelectedAssessment(projection);
      setSnapshots(snapList);
      setOverrides(overrideList);
      if (projection.controls.length > 0) {
        const first = projection.controls[0];
        if (first) {
          setEditingControlId(first.id);
          setControlStatusDraft(first.status);
          setControlWeightDraft(String(first.effective_weight));
          setControlRationaleDraft(first.rationale ?? '');
        }
      } else {
        setEditingControlId(null);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Unable to load assessment detail.',
      );
    }
  }, []);

  const loadAssessments = useCallback(async () => {
    if (!canReadRisks) return;
    setLoadingAssessments(true);
    setError(null);
    try {
      const list = await api.get<ComplianceAssessmentResponse[]>(
        '/compliance/assessments?limit=25&offset=0',
      );
      setAssessments(list);
      if (list[0]) {
        await loadAssessmentDetail(list[0].id);
      } else {
        setSelectedAssessment(null);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Unable to load compliance assessments.',
      );
    } finally {
      setLoadingAssessments(false);
    }
  }, [canReadRisks, loadAssessmentDetail]);

  const selectApproval = useCallback(async (approvalId: string) => {
    const reqId = ++approvalRequestSeqRef.current;
    decisionSeqRef.current += 1;
    requestedApprovalIdRef.current = approvalId;
    setRequestedApprovalId(approvalId);
    setSelectedApproval(null);
    setApprovalDetailError(null);
    setError(null);
    setSubmittingDecision(false);
    setLoadingApprovalDetail(true);
    setDecisionChoice('approved');
    setDecisionJustification('');
    setDecisionComment('');
    setModActionType('');
    setModResourceType('');
    setModResourceId('');
    setModSummary('');
    try {
      const detail = await api.get<PendingApproval | DecidedApproval>(
        `/approvals/${approvalId}`,
      );
      if (
        approvalRequestSeqRef.current !== reqId ||
        requestedApprovalIdRef.current !== approvalId
      ) {
        return;
      }
      setSelectedApproval(detail);
      setModActionType(detail.proposed_action.action_type);
      setModResourceType(detail.proposed_action.resource_type);
      setModResourceId(detail.proposed_action.resource_id);
      setModSummary(detail.proposed_action.summary);
    } catch (err) {
      if (
        approvalRequestSeqRef.current !== reqId ||
        requestedApprovalIdRef.current !== approvalId
      ) {
        return;
      }
      const message =
        err instanceof ApiError ? err.message : 'Unable to load approval detail.';
      setSelectedApproval(null);
      setApprovalDetailError(message);
    } finally {
      if (
        approvalRequestSeqRef.current === reqId &&
        requestedApprovalIdRef.current === approvalId
      ) {
        setLoadingApprovalDetail(false);
      }
    }
  }, []);

  const loadApprovals = useCallback(
    async (preserveSelection = false) => {
      if (!canReadApprovals) return;
      setLoadingApprovals(true);
      setError(null);
      try {
        const list = await api.get<PendingApproval[]>('/approvals?limit=25');
        setPendingApprovals(list);
        if (!preserveSelection) {
          if (list[0]) {
            await selectApproval(list[0].id);
          } else {
            setSelectedApproval(null);
          }
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Unable to load approval queue.');
      } finally {
        setLoadingApprovals(false);
      }
    },
    [canReadApprovals, selectApproval],
  );

  useEffect(() => {
    if (canReadRisks) void loadAssessments();
    if (canReadApprovals) void loadApprovals();
  }, [canReadRisks, canReadApprovals, loadAssessments, loadApprovals]);

  function selectControlForEdit(ctrl: ControlAssessmentProjection) {
    setEditingControlId(ctrl.id);
    setControlStatusDraft(ctrl.status);
    setControlWeightDraft(String(ctrl.effective_weight));
    setControlRationaleDraft(ctrl.rationale ?? '');
  }

  async function handleCreateAssessment(event: React.FormEvent) {
    event.preventDefault();
    if (!canCreateCompliance || creatingAssessment) return;
    setCreatingAssessment(true);
    setError(null);
    setStatusMessage(null);
    try {
      const created = await api.post<ComplianceAssessmentResponse>(
        '/compliance/assessments',
        {
          framework_id: newFrameworkId.trim(),
          title: newTitle.trim(),
        },
      );
      setNewTitle('');
      setStatusMessage(`Created compliance assessment "${created.title}".`);
      await loadAssessments();
      await loadAssessmentDetail(created.id);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Failed to create compliance assessment.',
      );
    } finally {
      setCreatingAssessment(false);
    }
  }

  async function handleSaveControl(event: React.FormEvent) {
    event.preventDefault();
    if (!editingControlId || !canCreateCompliance || savingControl) return;
    setSavingControl(true);
    setError(null);
    setStatusMessage(null);
    try {
      await api.patch(`/compliance/control-assessments/${editingControlId}`, {
        status: controlStatusDraft,
        effective_weight: controlWeightDraft,
        rationale: controlRationaleDraft.trim() || null,
      });
      setStatusMessage('Updated control assessment inputs.');
      if (selectedAssessment) {
        await loadAssessmentDetail(selectedAssessment.id);
      }
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'Failed to update control assessment.',
      );
    } finally {
      setSavingControl(false);
    }
  }

  async function handleAdmitEvidence(event: React.FormEvent) {
    event.preventDefault();
    if (!editingControlId || !canCreateCompliance || admittingEvidence) return;
    setAdmittingEvidence(true);
    setError(null);
    setStatusMessage(null);
    try {
      await api.post(`/compliance/control-assessments/${editingControlId}/evidence`, {
        document_id: evidenceDocId.trim(),
        chunk_id: evidenceChunkId.trim() || null,
      });
      setEvidenceDocId('');
      setEvidenceChunkId('');
      setStatusMessage('Admitted validated evidence reference.');
      if (selectedAssessment) {
        await loadAssessmentDetail(selectedAssessment.id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to admit evidence.');
    } finally {
      setAdmittingEvidence(false);
    }
  }

  async function handleComputeScore() {
    if (!selectedAssessment || !canCreateCompliance || runningAction) return;
    setRunningAction(true);
    setError(null);
    setStatusMessage(null);
    try {
      const result = await api.post<AssessmentScoreResult>(
        `/compliance/assessments/${selectedAssessment.id}/compute`,
        {},
      );
      setStatusMessage(
        `Computed deterministic score (${result.overall_score ?? 'unscored'}, ${result.risk_classification}).`,
      );
      await loadAssessmentDetail(selectedAssessment.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Score computation failed.');
    } finally {
      setRunningAction(false);
    }
  }

  async function handleExtractCandidates() {
    if (!selectedAssessment || !canCreateCompliance || runningAction) return;
    setRunningAction(true);
    setError(null);
    setStatusMessage(null);
    try {
      const result = await api.post<ComplianceCandidateExtractionResult>(
        `/compliance/assessments/${selectedAssessment.id}/candidates`,
        { assessment_id: selectedAssessment.id },
      );
      setCandidates(result);
      setStatusMessage(
        `Extracted ${result.findings.length} non-authoritative candidate finding(s) via ${result.model_id}.`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Candidate extraction failed.');
    } finally {
      setRunningAction(false);
    }
  }

  async function handleFinalizeAssessment() {
    if (!selectedAssessment || !canReviewRisk || runningAction) return;
    setRunningAction(true);
    setError(null);
    setStatusMessage(null);
    try {
      const finalized = await api.post<ComplianceAssessmentResponse>(
        `/compliance/assessments/${selectedAssessment.id}/finalize`,
        {},
      );
      setStatusMessage(`Assessment "${finalized.title}" finalized (${finalized.status}).`);
      await loadAssessmentDetail(selectedAssessment.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to finalize assessment.');
    } finally {
      setRunningAction(false);
    }
  }

  async function handleCreateOverride(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedAssessment || !canOverrideRisk || submittingOverride) return;
    setSubmittingOverride(true);
    setError(null);
    setStatusMessage(null);
    try {
      await api.post<ScoreOverrideResponse>(
        `/compliance/assessments/${selectedAssessment.id}/override`,
        {
          override_overall_score: overrideScore.trim(),
          justification: overrideJustification.trim(),
        },
      );
      setOverrideJustification('');
      setStatusMessage('Recorded immutable Manager score override.');
      await loadAssessmentDetail(selectedAssessment.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to record score override.');
    } finally {
      setSubmittingOverride(false);
    }
  }

  async function handleSubmitDecision(event: React.FormEvent) {
    event.preventDefault();
    if (
      !selectedApproval ||
      !canDecideApprovals ||
      submittingDecision ||
      loadingApprovalDetail
    ) {
      return;
    }
    const targetApprovalId = selectedApproval.id;
    const reqSeq = approvalRequestSeqRef.current;
    const decisionSeq = ++decisionSeqRef.current;
    setSubmittingDecision(true);
    setError(null);
    setStatusMessage(null);

    const payload: ApprovalDecisionRequest = {
      decision: decisionChoice,
      justification: decisionJustification.trim() || null,
      comment: decisionComment.trim() || null,
      modified_action:
        decisionChoice === 'modified'
          ? {
              action_type: modActionType.trim(),
              resource_type: modResourceType.trim(),
              resource_id: modResourceId.trim(),
              summary: modSummary.trim(),
            }
          : null,
    };

    try {
      const decided = await api.post<DecidedApproval>(
        `/approvals/${targetApprovalId}/decision`,
        payload,
      );
      if (
        approvalRequestSeqRef.current !== reqSeq ||
        decisionSeqRef.current !== decisionSeq ||
        requestedApprovalIdRef.current !== targetApprovalId
      ) {
        return;
      }
      setSelectedApproval(decided);
      setDecisionJustification('');
      setDecisionComment('');
      setStatusMessage(
        decided.status === 'execution_succeeded'
          ? `Recorded Manager decision: ${decided.decision} (execution succeeded).`
          : `Recorded Manager decision: ${decided.decision} (status: ${decided.status}; downstream execution has not succeeded yet).`,
      );
      await loadApprovals(true);
    } catch (err) {
      if (
        approvalRequestSeqRef.current !== reqSeq ||
        decisionSeqRef.current !== decisionSeq ||
        requestedApprovalIdRef.current !== targetApprovalId
      ) {
        return;
      }
      setError(
        err instanceof ApiError ? err.message : 'Failed to submit approval decision.',
      );
    } finally {
      if (
        approvalRequestSeqRef.current === reqSeq &&
        decisionSeqRef.current === decisionSeq &&
        requestedApprovalIdRef.current === targetApprovalId
      ) {
        setSubmittingDecision(false);
      }
    }
  }

  if (!canReadRisks && !canReadApprovals) {
    return (
      <div className="mx-auto max-w-4xl">
        <div className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-6">
          <h1 className="text-lg font-semibold text-[var(--color-ink)]">
            Risk &amp; Approvals
          </h1>
          <p className="mt-2 text-sm text-[var(--color-ink-muted)]">
            Your role does not include <span className="font-mono">risk:read</span> or{' '}
            <span className="font-mono">approval:read</span> permissions.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-col gap-3 border-b border-[var(--color-line)] pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-accent)]">
            Deterministic Risk &amp; Human Gate
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-[var(--color-ink)]">
            Compliance Assessments &amp; Approvals
          </h1>
          <p className="mt-1 text-sm text-[var(--color-ink-muted)]">
            Clearance-projected control assessments, deterministic scoring snapshots,
            and segregation-of-duties Manager approval workflows.
          </p>
        </div>

        <div className="flex gap-2" role="tablist" aria-label="Risk workspace views">
          {canReadRisks && (
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'assessments'}
              onClick={() => setActiveTab('assessments')}
              className={`inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-3 py-1.5 text-xs font-medium ${
                activeTab === 'assessments'
                  ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                  : 'border-[var(--color-line-strong)] bg-[var(--color-surface)] text-[var(--color-ink-muted)]'
              }`}
            >
              <ShieldAlert size={14} aria-hidden />
              <span>Assessments ({assessments.length})</span>
            </button>
          )}
          {canReadApprovals && (
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'approvals'}
              onClick={() => setActiveTab('approvals')}
              className={`inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] border px-3 py-1.5 text-xs font-medium ${
                activeTab === 'approvals'
                  ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                  : 'border-[var(--color-line-strong)] bg-[var(--color-surface)] text-[var(--color-ink-muted)]'
              }`}
            >
              <ClipboardCheck size={14} aria-hidden />
              <span>Approvals ({pendingApprovals.length})</span>
            </button>
          )}
        </div>
      </header>

      {error && (
        <div
          role="alert"
          className="border-l-[3px] border-l-[var(--color-error)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-ink)] shadow-xs"
        >
          {error}
        </div>
      )}

      {statusMessage && (
        <div
          role="status"
          className="border-l-[3px] border-l-[var(--color-ok)] bg-[var(--color-surface)] px-4 py-3 text-sm text-[var(--color-ink)] shadow-xs"
        >
          {statusMessage}
        </div>
      )}

      {activeTab === 'assessments' && canReadRisks && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-4">
            <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
              <h2 className="text-sm font-semibold text-[var(--color-ink)]">
                Tenant Assessments
              </h2>
              {loadingAssessments ? (
                <p className="mt-3 text-xs text-[var(--color-ink-muted)]">Loading…</p>
              ) : assessments.length === 0 ? (
                <p className="mt-3 text-xs text-[var(--color-ink-muted)]">
                  No compliance assessments exist for your tenant yet.
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {assessments.map((item) => {
                    const effectiveClass =
                      item.effective_risk_classification ?? item.risk_classification;
                    const isSelected = selectedAssessment?.id === item.id;
                    return (
                      <li key={item.id}>
                        <button
                          type="button"
                          onClick={() => void loadAssessmentDetail(item.id)}
                          className={`w-full rounded-[var(--radius-sm)] border border-l-[3px] p-3 text-left transition-colors ${
                            SEVERITY_RULE[effectiveClass]
                          } ${
                            isSelected
                              ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)]'
                              : 'border-[var(--color-line)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-sunken)]'
                          }`}
                        >
                          <p className="truncate text-sm font-medium text-[var(--color-ink)]">
                            {item.title}
                          </p>
                          <p className="mt-1 font-mono text-[11px] text-[var(--color-ink-muted)]">
                            {item.status} · score:{' '}
                            {item.effective_overall_score ?? item.overall_score ?? 'N/A'} (
                            {effectiveClass})
                          </p>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            {canCreateCompliance && (
              <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
                <h2 className="text-sm font-semibold text-[var(--color-ink)]">
                  Initialize Assessment
                </h2>
                <form
                  onSubmit={(e) => void handleCreateAssessment(e)}
                  className="mt-3 space-y-3 text-xs"
                >
                  <div>
                    <label htmlFor="new-framework-id" className="block font-medium">
                      Framework UUID
                    </label>
                    <input
                      id="new-framework-id"
                      type="text"
                      required
                      value={newFrameworkId}
                      onChange={(e) => setNewFrameworkId(e.target.value)}
                      placeholder="UUID of seeded compliance framework"
                      className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5 font-mono text-[11px]"
                    />
                  </div>
                  <div>
                    <label htmlFor="new-assessment-title" className="block font-medium">
                      Assessment title
                    </label>
                    <input
                      id="new-assessment-title"
                      type="text"
                      required
                      value={newTitle}
                      onChange={(e) => setNewTitle(e.target.value)}
                      placeholder="Q4 Access Control Review"
                      className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={creatingAssessment}
                    className="w-full rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-3 py-2 text-xs font-medium text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
                  >
                    {creatingAssessment ? 'Creating…' : 'Create assessment'}
                  </button>
                </form>
              </section>
            )}
          </div>

          <div className="space-y-4 lg:col-span-2">
            {selectedAssessment ? (
              <>
                <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h2 className="text-lg font-semibold text-[var(--color-ink)]">
                        {selectedAssessment.title}
                      </h2>
                      <p className="mt-0.5 font-mono text-[11px] text-[var(--color-ink-subtle)]">
                        id: {selectedAssessment.id} · status: {selectedAssessment.status}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs text-[var(--color-ink-muted)]">
                        Effective Score
                      </p>
                      <p className="numeric text-xl font-semibold text-[var(--color-ink)]">
                        {selectedAssessment.effective_overall_score ??
                          selectedAssessment.overall_score ??
                          'Unscored'}
                      </p>
                      <p className="font-mono text-[11px] uppercase text-[var(--color-ink-muted)]">
                        {selectedAssessment.effective_risk_classification ??
                          selectedAssessment.risk_classification}
                      </p>
                    </div>
                  </div>

                  {selectedAssessment.any_hidden_evidence && (
                    <p className="mt-3 rounded-[var(--radius-sm)] border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                      Some evidence references exceed your confidentiality clearance and
                      are omitted from this projection.
                    </p>
                  )}

                  <div className="mt-4 flex flex-wrap gap-2 border-t border-[var(--color-line)] pt-4">
                    {canCreateCompliance && (
                      <>
                        <button
                          type="button"
                          disabled={runningAction}
                          onClick={() => void handleComputeScore()}
                          className="rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
                        >
                          Compute deterministic score
                        </button>
                        <button
                          type="button"
                          disabled={runningAction}
                          onClick={() => void handleExtractCandidates()}
                          className="inline-flex items-center gap-1 rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink)] hover:bg-[var(--color-surface-sunken)] disabled:opacity-50"
                        >
                          <Sparkles size={13} aria-hidden />
                          <span>Extract LLM candidates (non-authoritative)</span>
                        </button>
                      </>
                    )}
                    {canReviewRisk && selectedAssessment.status !== 'completed' && (
                      <button
                        type="button"
                        disabled={runningAction}
                        onClick={() => void handleFinalizeAssessment()}
                        className="inline-flex items-center gap-1 rounded-[var(--radius-sm)] bg-[var(--color-rail)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-rail-hover)] disabled:opacity-50"
                      >
                        <CheckCircle2 size={13} aria-hidden />
                        <span>Finalize assessment (Manager)</span>
                      </button>
                    )}
                  </div>
                </section>

                <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
                  <h3 className="text-sm font-semibold text-[var(--color-ink)]">
                    Controls ({selectedAssessment.controls.length})
                  </h3>
                  <div className="mt-3 space-y-2">
                    {selectedAssessment.controls.map((ctrl) => (
                      <div
                        key={ctrl.id}
                        className={`rounded-[var(--radius-sm)] border p-3 text-xs ${
                          editingControlId === ctrl.id
                            ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)]'
                            : 'border-[var(--color-line)]'
                        }`}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-mono text-[11px] font-medium">
                            control: {ctrl.control_id}
                          </span>
                          <div className="flex items-center gap-2 font-mono text-[11px]">
                            <span>{ctrl.status}</span>
                            <span>wt: {ctrl.effective_weight}</span>
                            <span>ev: {ctrl.evidence.length}</span>
                            {canCreateCompliance && (
                              <button
                                type="button"
                                onClick={() => selectControlForEdit(ctrl)}
                                className="text-[var(--color-accent)] underline"
                              >
                                Edit
                              </button>
                            )}
                          </div>
                        </div>
                        {ctrl.rationale && (
                          <p className="mt-1 text-[var(--color-ink-muted)]">
                            {ctrl.rationale}
                          </p>
                        )}
                        {ctrl.hidden_evidence_present && (
                          <p className="mt-1 font-mono text-[10px] text-amber-800">
                            Restricted evidence omitted due to caller clearance.
                          </p>
                        )}
                      </div>
                    ))}
                  </div>

                  {canCreateCompliance && editingControlId && (
                    <div className="mt-5 grid gap-4 border-t border-[var(--color-line)] pt-4 sm:grid-cols-2">
                      <form
                        onSubmit={(e) => void handleSaveControl(e)}
                        className="space-y-2.5 text-xs"
                      >
                        <h4 className="font-semibold text-[var(--color-ink)]">
                          Update Selected Control
                        </h4>
                        <div>
                          <label htmlFor="ctrl-status" className="block font-medium">
                            Status
                          </label>
                          <select
                            id="ctrl-status"
                            value={controlStatusDraft}
                            onChange={(e) =>
                              setControlStatusDraft(e.target.value as ControlStatus)
                            }
                            className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2 py-1.5"
                          >
                            {CONTROL_STATUSES.map((s) => (
                              <option key={s} value={s}>
                                {s}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div>
                          <label htmlFor="ctrl-weight" className="block font-medium">
                            Weight (1.0 – 5.0)
                          </label>
                          <input
                            id="ctrl-weight"
                            type="text"
                            value={controlWeightDraft}
                            onChange={(e) => setControlWeightDraft(e.target.value)}
                            className="numeric mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                          />
                        </div>
                        <div>
                          <label htmlFor="ctrl-rationale" className="block font-medium">
                            Rationale
                          </label>
                          <textarea
                            id="ctrl-rationale"
                            rows={2}
                            value={controlRationaleDraft}
                            onChange={(e) => setControlRationaleDraft(e.target.value)}
                            className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                          />
                        </div>
                        <button
                          type="submit"
                          disabled={savingControl}
                          className="rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
                        >
                          {savingControl ? 'Saving…' : 'Save control'}
                        </button>
                      </form>

                      <form
                        onSubmit={(e) => void handleAdmitEvidence(e)}
                        className="space-y-2.5 text-xs"
                      >
                        <h4 className="font-semibold text-[var(--color-ink)]">
                          Admit Evidence Reference
                        </h4>
                        <div>
                          <label htmlFor="ev-doc-id" className="block font-medium">
                            Document UUID
                          </label>
                          <input
                            id="ev-doc-id"
                            type="text"
                            required
                            value={evidenceDocId}
                            onChange={(e) => setEvidenceDocId(e.target.value)}
                            className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5 font-mono text-[11px]"
                          />
                        </div>
                        <div>
                          <label htmlFor="ev-chunk-id" className="block font-medium">
                            Chunk UUID (optional)
                          </label>
                          <input
                            id="ev-chunk-id"
                            type="text"
                            value={evidenceChunkId}
                            onChange={(e) => setEvidenceChunkId(e.target.value)}
                            className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5 font-mono text-[11px]"
                          />
                        </div>
                        <button
                          type="submit"
                          disabled={admittingEvidence}
                          className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink)] hover:bg-[var(--color-surface-sunken)] disabled:opacity-50"
                        >
                          {admittingEvidence ? 'Admitting…' : 'Admit evidence'}
                        </button>
                      </form>
                    </div>
                  )}
                </section>

                {candidates && (
                  <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
                    <h3 className="text-sm font-semibold text-[var(--color-ink)]">
                      Non-Authoritative Candidate Findings ({candidates.findings.length})
                    </h3>
                    <p className="mt-0.5 text-xs text-[var(--color-ink-muted)]">
                      Proposals only (model: {candidates.model_id}); deterministic score
                      remains unchanged until controls are updated.
                    </p>
                    <ul className="mt-3 space-y-2 text-xs">
                      {candidates.findings.map((f, idx) => (
                        <li
                          key={`${f.control_id}-${idx}`}
                          className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] p-3"
                        >
                          <p className="font-mono text-[11px]">
                            control: {f.control_id} · proposed: {f.proposed_status}
                          </p>
                          <p className="mt-1 text-[var(--color-ink)]">{f.rationale}</p>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                <div className="grid gap-4 sm:grid-cols-2">
                  <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
                    <h3 className="text-sm font-semibold text-[var(--color-ink)]">
                      Score Snapshots ({snapshots.length})
                    </h3>
                    {snapshots.length === 0 ? (
                      <p className="mt-2 text-xs text-[var(--color-ink-muted)]">
                        No score snapshots computed yet.
                      </p>
                    ) : (
                      <ul className="mt-2 space-y-2 text-xs">
                        {snapshots.map((snap) => (
                          <li
                            key={snap.id}
                            className="rounded-[var(--radius-sm)] border border-[var(--color-line)] p-2.5 font-mono text-[11px]"
                          >
                            rev #{snap.revision_number} · score:{' '}
                            {snap.overall_score ?? 'N/A'} ({snap.risk_classification})
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>

                  <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
                    <h3 className="text-sm font-semibold text-[var(--color-ink)]">
                      Human Overrides ({overrides.length})
                    </h3>
                    {overrides.length > 0 && (
                      <ul className="mt-2 space-y-2 text-xs">
                        {overrides.map((ov) => (
                          <li
                            key={ov.id}
                            className="rounded-[var(--radius-sm)] border border-[var(--color-line)] p-2.5"
                          >
                            <p className="font-mono text-[11px]">
                              {ov.original_overall_score ?? 'N/A'} →{' '}
                              {ov.override_overall_score} ({ov.override_risk_classification})
                            </p>
                            <p className="mt-1 text-[var(--color-ink-muted)]">
                              {ov.justification}
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}

                    {canOverrideRisk ? (
                      <form
                        onSubmit={(e) => void handleCreateOverride(e)}
                        className="mt-3 space-y-2 border-t border-[var(--color-line)] pt-3 text-xs"
                      >
                        <p className="font-medium text-[var(--color-ink)]">
                          Manager Score Override
                        </p>
                        <div>
                          <label htmlFor="override-score" className="block">
                            Override score (0.00 – 100.00)
                          </label>
                          <input
                            id="override-score"
                            type="text"
                            required
                            value={overrideScore}
                            onChange={(e) => setOverrideScore(e.target.value)}
                            className="numeric mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                          />
                        </div>
                        <div>
                          <label htmlFor="override-just" className="block">
                            Mandatory justification
                          </label>
                          <textarea
                            id="override-just"
                            rows={2}
                            required
                            value={overrideJustification}
                            onChange={(e) => setOverrideJustification(e.target.value)}
                            className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                          />
                        </div>
                        <button
                          type="submit"
                          disabled={submittingOverride}
                          className="w-full rounded-[var(--radius-sm)] bg-[var(--color-rail)] px-3 py-1.5 text-xs font-medium text-white hover:bg-[var(--color-rail-hover)] disabled:opacity-50"
                        >
                          {submittingOverride ? 'Recording…' : 'Record score override'}
                        </button>
                      </form>
                    ) : (
                      <p className="mt-3 text-[11px] text-[var(--color-ink-subtle)]">
                        Score overrides require <span className="font-mono">risk:modify_severity</span>{' '}
                        (Manager only).
                      </p>
                    )}
                  </section>
                </div>
              </>
            ) : (
              <div className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-6 text-sm text-[var(--color-ink-muted)]">
                Select or create a compliance assessment to inspect controls and scoring.
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'approvals' && canReadApprovals && (
        <div className="grid gap-6 lg:grid-cols-3">
          <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
            <h2 className="text-sm font-semibold text-[var(--color-ink)]">
              Pending Approval Queue
            </h2>
            {loadingApprovals ? (
              <p className="mt-3 text-xs text-[var(--color-ink-muted)]">Loading…</p>
            ) : pendingApprovals.length === 0 ? (
              <p className="mt-3 text-xs text-[var(--color-ink-muted)]">
                No pending approvals in queue.
              </p>
            ) : (
              <ul className="mt-3 space-y-2">
                {pendingApprovals.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => void selectApproval(item.id)}
                      className={`w-full rounded-[var(--radius-sm)] border p-3 text-left text-xs ${
                        requestedApprovalId === item.id
                          ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)]'
                          : 'border-[var(--color-line)] hover:bg-[var(--color-surface-sunken)]'
                      }`}
                    >
                      <p className="font-medium text-[var(--color-ink)]">
                        {item.proposed_action.action_type}
                      </p>
                      <p className="mt-1 line-clamp-2 text-[var(--color-ink-muted)]">
                        {item.proposed_action.summary}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="rounded-[var(--radius-md)] border border-[var(--color-line)] bg-[var(--color-surface)] p-5 lg:col-span-2">
            {loadingApprovalDetail ? (
              <p className="text-sm text-[var(--color-ink-muted)]">
                Loading approval detail…
              </p>
            ) : approvalDetailError ? (
              <div role="alert" className="space-y-3 text-sm">
                <p className="border-l-[3px] border-l-[var(--color-error)] bg-[var(--color-surface-sunken)] px-3 py-2 text-[var(--color-ink)]">
                  {approvalDetailError}
                </p>
                {requestedApprovalId && (
                  <button
                    type="button"
                    onClick={() => void selectApproval(requestedApprovalId)}
                    className="rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-ink)] hover:bg-[var(--color-surface-sunken)]"
                  >
                    Retry loading approval
                  </button>
                )}
              </div>
            ) : selectedApproval ? (
              <div className="space-y-4">
                <div>
                  <h2 className="text-base font-semibold text-[var(--color-ink)]">
                    Proposed Action: {selectedApproval.proposed_action.action_type}
                  </h2>
                  <p className="mt-1 font-mono text-[11px] text-[var(--color-ink-subtle)]">
                    approval: {selectedApproval.id} · status: {selectedApproval.status}
                  </p>
                  <p className="mt-3 rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-canvas)] p-3 text-sm text-[var(--color-ink)]">
                    {selectedApproval.proposed_action.summary}
                  </p>
                </div>

                <div className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] p-3 text-xs">
                  <p className="font-semibold text-[var(--color-ink)]">
                    Evidence References ({selectedApproval.evidence.length})
                  </p>
                  {selectedApproval.evidence.length === 0 ? (
                    <p className="mt-1 text-[var(--color-ink-muted)]">
                      No evidence references attached.
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-1 font-mono text-[11px] text-[var(--color-ink-muted)]">
                      {selectedApproval.evidence.map((ev) => (
                        <li key={ev.chunk_id}>
                          doc: {ev.document_id} · chunk: {ev.chunk_id}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {selectedApproval.score_context && (
                  <div className="rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] p-3 text-xs">
                    <p className="font-semibold">
                      Deterministic Score Context: {selectedApproval.score_context.score}{' '}
                      ({selectedApproval.score_context.scoring_version})
                    </p>
                    <p className="mt-1 font-mono text-[11px] text-[var(--color-ink-muted)]">
                      controls:{' '}
                      {selectedApproval.score_context.component_breakdown.applicable_controls}
                      /{selectedApproval.score_context.component_breakdown.total_controls}{' '}
                      · critical override:{' '}
                      {String(
                        selectedApproval.score_context.component_breakdown
                          .critical_override_triggered,
                      )}
                    </p>
                  </div>
                )}

                {canDecideApprovals && selectedApproval.status === 'pending' ? (
                  <form
                    onSubmit={(e) => void handleSubmitDecision(e)}
                    className="space-y-3 border-t border-[var(--color-line)] pt-4 text-xs"
                  >
                    <h3 className="text-sm font-semibold text-[var(--color-ink)]">
                      Record Manager Decision
                    </h3>
                    <div>
                      <label htmlFor="approval-decision" className="block font-medium">
                        Decision
                      </label>
                      <select
                        id="approval-decision"
                        value={decisionChoice}
                        onChange={(e) =>
                          setDecisionChoice(e.target.value as ApprovalDecision)
                        }
                        className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 py-1.5"
                      >
                        <option value="approved">approved</option>
                        <option value="rejected">rejected</option>
                        <option value="modified">modified</option>
                      </select>
                    </div>

                    <div>
                      <label htmlFor="approval-just" className="block font-medium">
                        Justification{' '}
                        {decisionChoice !== 'approved' ? '(required)' : '(optional)'}
                      </label>
                      <textarea
                        id="approval-just"
                        rows={2}
                        required={decisionChoice !== 'approved'}
                        value={decisionJustification}
                        onChange={(e) => setDecisionJustification(e.target.value)}
                        className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                      />
                    </div>

                    <div>
                      <label htmlFor="approval-comment" className="block font-medium">
                        Comment (optional)
                      </label>
                      <input
                        id="approval-comment"
                        type="text"
                        value={decisionComment}
                        onChange={(e) => setDecisionComment(e.target.value)}
                        className="mt-1 w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] px-2.5 py-1.5"
                      />
                    </div>

                    {decisionChoice === 'modified' && (
                      <div className="space-y-2 rounded-[var(--radius-sm)] border border-[var(--color-line)] bg-[var(--color-surface-sunken)] p-3">
                        <p className="font-medium">Replacement Action</p>
                        <input
                          type="text"
                          required
                          placeholder="action_type"
                          value={modActionType}
                          onChange={(e) => setModActionType(e.target.value)}
                          className="w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 py-1.5"
                        />
                        <input
                          type="text"
                          required
                          placeholder="resource_type"
                          value={modResourceType}
                          onChange={(e) => setModResourceType(e.target.value)}
                          className="w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 py-1.5"
                        />
                        <input
                          type="text"
                          required
                          placeholder="resource_id (UUID)"
                          value={modResourceId}
                          onChange={(e) => setModResourceId(e.target.value)}
                          className="w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 py-1.5 font-mono text-[11px]"
                        />
                        <textarea
                          rows={2}
                          required
                          placeholder="summary"
                          value={modSummary}
                          onChange={(e) => setModSummary(e.target.value)}
                          className="w-full rounded-[var(--radius-sm)] border border-[var(--color-line-strong)] bg-[var(--color-surface)] px-2.5 py-1.5"
                        />
                      </div>
                    )}

                    <button
                      type="submit"
                      disabled={submittingDecision}
                      className="rounded-[var(--radius-sm)] bg-[var(--color-accent)] px-4 py-2 text-xs font-medium text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
                    >
                      {submittingDecision ? 'Submitting…' : 'Submit decision'}
                    </button>
                  </form>
                ) : (
                  <p className="border-t border-[var(--color-line)] pt-3 text-xs text-[var(--color-ink-muted)]">
                    {selectedApproval.status === 'pending'
                      ? 'Read-only view: recording approval decisions requires approval:decide (Manager only).'
                      : `Decision recorded: ${(selectedApproval as DecidedApproval).decision}.`}
                  </p>
                )}
              </div>
            ) : (
              <p className="text-sm text-[var(--color-ink-muted)]">
                Select a pending approval to inspect evidence, score context, and
                decision state.
              </p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
