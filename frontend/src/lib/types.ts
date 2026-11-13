/**
 * API contract types.
 *
 * Hand-written in Phase 1 because there are three of them. Once the surface
 * grows past a handful, generate these from the backend's OpenAPI schema
 * (`openapi-typescript`) rather than maintaining two sources of truth that
 * drift — a mismatched frontend type is a runtime error the compiler
 * cheerfully approved.
 */

export type Role = 'analyst' | 'manager' | 'admin';

export interface HealthStatus {
  status: string;
  environment: string;
  version: string;
  checked_at: string;
  dependencies: Record<string, string>;
}

export interface Me {
  user_id: string;
  organization_id: string;
  email: string;
  role: Role;
  department: string | null;
  permissions: string[];
  visible_confidentiality_levels: string[];
}

export interface AuthConfig {
  provider: 'local' | 'cognito';
  issuer: string;
  hosted_ui_domain: string | null;
  client_id: string | null;
  scopes: string[];
  local_login_enabled: boolean;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown> | null;
    request_id?: string | null;
  };
}

export type EvaluationCategory =
  | 'answerable'
  | 'unanswerable'
  | 'ambiguous'
  | 'cross_document'
  | 'unauthorized'
  | 'prompt_injection';

export type EvaluationExecutionMode =
  | 'deterministic_fixture'
  | 'local_pgvector_fixture'
  | 'live_bedrock';

export interface EvaluationDenominators {
  total_cases: number;
  retrieval_evaluated_cases: number;
  citation_evaluated_cases: number;
  groundedness_evaluated_cases: number;
  answer_evaluated_cases: number;
  abstention_evaluated_cases: number;
  unauthorized_evaluated_cases: number;
  prompt_injection_evaluated_cases: number;
  agent_evaluated_cases: number;
  tool_call_evaluated_count: number;
}

export interface EvaluationMetricsSummary {
  precision_at_5: number;
  recall_at_10: number;
  context_relevance_score: number;
  groundedness_score: number;
  answer_relevance_score: number;
  citation_accuracy: number;
  hallucination_rate: number;
  unsafe_response_rate: number;
  unanswerable_abstention_rate: number;
  cross_tenant_leak_count: number;
  unauthorized_disclosure_count: number;
  prompt_injection_breach_count: number;
  adversarial_corpus_pass_rate: number;
  tool_call_success_rate: number;
  agent_task_completion_rate: number;
  retrieval_latency_p50_ms: number;
  retrieval_latency_p95_ms: number;
  end_to_end_latency_p50_ms: number;
  end_to_end_latency_p95_ms: number;
  cost_per_1000_queries_usd: number | null;
  denominators: EvaluationDenominators;
}

export interface CategoryEvaluationSummary {
  category: EvaluationCategory;
  case_count: number;
  passed_count: number;
  pass_rate: number;
  avg_precision_at_5: number | null;
  avg_recall_at_10: number | null;
  avg_groundedness_score: number | null;
  avg_answer_relevance_score: number | null;
  avg_citation_accuracy: number | null;
  hallucination_rate: number | null;
  unsafe_response_rate: number;
}

export interface EvaluationCaseResult {
  case_id: string;
  category: EvaluationCategory;
  passed: boolean;
  retrieved_chunk_ids: string[];
  cited_chunk_ids: string[];
  abstained: boolean;
  precision_at_5: number | null;
  recall_at_10: number | null;
  context_relevance_score: number | null;
  groundedness_score: number | null;
  answer_relevance_score: number | null;
  citation_accuracy: number | null;
  hallucinated: boolean;
  unsafe_response: boolean;
  cross_tenant_leak_detected: boolean;
  unauthorized_disclosure_detected: boolean;
  prompt_injection_breach_detected: boolean;
  agent_workflow_attempted: boolean;
  agent_workflow_completed: boolean;
  tool_calls_total: number;
  tool_calls_succeeded: number;
  retrieval_latency_ms: number;
  end_to_end_latency_ms: number;
  failure_reason: string | null;
}

export interface EvaluationRunSummary {
  id: string;
  organization_id: string;
  triggered_by: string;
  dataset_version: string;
  execution_mode: EvaluationExecutionMode;
  embedding_model_id: string;
  generation_model_id: string;
  reviewer_model_id: string;
  judge_model_id: string;
  security_gate_passed: boolean;
  quality_gate_advisory_passed: boolean;
  metrics_summary: EvaluationMetricsSummary;
  category_breakdown: CategoryEvaluationSummary[];
  provenance_notes: string[];
  started_at: string;
  completed_at: string;
  created_at: string;
}

export interface EvaluationRunDetail extends EvaluationRunSummary {
  case_results: EvaluationCaseResult[];
}

export interface EvaluationRunListResponse {
  items: EvaluationRunSummary[];
  total: number;
  visibility_scope: 'own' | 'team';
}

export interface EvaluationDatasetMetadata {
  dataset_version: string;
  golden_case_count: number;
  adversarial_case_count: number;
  category_counts: Record<string, number>;
  metric_definitions: Record<string, string>;
  known_limitations: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type DocumentType =
  | 'policy'
  | 'audit_report'
  | 'control_documentation'
  | 'financial_report'
  | 'invoice'
  | 'erp_export'
  | 'sop'
  | 'risk_report'
  | 'vendor_document'
  | 'contract'
  | 'security_policy'
  | 'unknown';

export type ConfidentialityLevel = 'public' | 'internal' | 'confidential' | 'restricted';

export type ProcessingStatus =
  | 'queued'
  | 'extracting'
  | 'indexing'
  | 'ready'
  | 'failed'
  | 'quarantined';

export interface DocumentCreateRequest {
  filename: string;
  content_type: string;
  size_bytes: number;
  document_type: DocumentType;
  confidentiality_level: ConfidentialityLevel;
  department?: string | null;
  source?: string | null;
  tags?: string[];
}

export interface DocumentResponse {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  document_type: DocumentType;
  confidentiality_level: ConfidentialityLevel;
  processing_status: ProcessingStatus;
  processing_error: string | null;
  uploader_id: string;
  department: string | null;
  source: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface DocumentUploadResponse {
  document: DocumentResponse;
  upload_url: string;
  expires_in_seconds: number;
}

export interface VectorMatch {
  chunk_id: string;
  document_id: string;
  score: number;
  content: string;
  metadata: Record<string, unknown>;
}

export interface RetrievalRequest {
  query: string;
  top_k?: number;
  document_ids?: string[] | null;
}

export interface RetrievalResponse {
  matches: VectorMatch[];
  total: number;
}

export interface TokenUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens?: number;
  total_tokens?: number | null;
}

export interface RAGRequest {
  query: string;
  top_k?: number;
  document_ids?: string[] | null;
}

export interface RAGSource {
  label: string;
  chunk_id: string;
  document_id: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface RAGResponse {
  answer: string;
  sources: RAGSource[];
  retrieval_count: number;
  model_id: string;
  usage: TokenUsage | null;
}

export type RiskClassification = 'not_scored' | 'low' | 'medium' | 'high' | 'critical';

export type ControlStatus =
  | 'satisfied'
  | 'partially_satisfied'
  | 'deficient'
  | 'unassessed'
  | 'not_applicable';

export type AssessmentStatus = 'draft' | 'in_progress' | 'completed' | 'archived';

export interface ScoreOverrideResponse {
  id: string;
  organization_id: string;
  assessment_id: string;
  snapshot_id: string;
  source_revision_number: number;
  original_overall_score: string | number | null;
  original_risk_classification: RiskClassification;
  override_overall_score: string | number;
  override_risk_classification: RiskClassification;
  justification: string;
  overridden_by: string;
  overridden_at: string;
}

export interface ComplianceAssessmentResponse {
  id: string;
  organization_id: string;
  framework_id: string;
  title: string;
  status: AssessmentStatus;
  overall_score: string | number | null;
  risk_classification: RiskClassification;
  scoring_version: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  effective_overall_score?: string | number | null;
  effective_risk_classification?: RiskClassification | null;
  latest_override?: ScoreOverrideResponse | null;
}

export interface VisibleEvidenceReference {
  id: string;
  control_assessment_id: string;
  document_id: string;
  chunk_id: string | null;
  confidentiality_level: ConfidentialityLevel;
  snippet: string | null;
  created_by: string;
  created_at: string;
}

export interface ControlAssessmentProjection {
  id: string;
  organization_id: string;
  assessment_id: string;
  control_id: string;
  status: ControlStatus;
  effective_weight: string | number;
  rationale: string | null;
  created_at: string;
  updated_at: string;
  evidence: VisibleEvidenceReference[];
  hidden_evidence_present: boolean;
}

export interface ComplianceAssessmentProjection extends ComplianceAssessmentResponse {
  controls: ControlAssessmentProjection[];
  any_hidden_evidence: boolean;
}

export interface ControlAssessmentUpdateRequest {
  status?: ControlStatus;
  effective_weight?: number | string;
  rationale?: string | null;
}

export interface ControlAssessmentResponse {
  id: string;
  organization_id: string;
  assessment_id: string;
  control_id: string;
  status: ControlStatus;
  effective_weight: string | number;
  rationale: string | null;
  created_at: string;
  updated_at: string;
  evidence_count: number;
}

export interface AssessmentScoreResult {
  scoring_version: string;
  framework_id: string;
  framework_version: string;
  applicable_control_count: number;
  total_control_count: number;
  overall_score: string | number | null;
  residual_risk: string | number | null;
  risk_classification: RiskClassification;
  critical_override_triggered: boolean;
}

export interface AssessmentScoreSnapshotProjection {
  id: string;
  organization_id: string;
  assessment_id: string;
  revision_number: number;
  scoring_version: string;
  framework_version: string;
  raw_scores: Record<string, unknown>;
  overall_score: string | number | null;
  risk_classification: RiskClassification;
  computed_by: string | null;
  computed_at: string;
}

export interface CandidateEvidenceReference {
  chunk_id: string;
  document_id: string;
  quote: string;
  relevance_explanation: string | null;
  confidence: number | null;
}

export interface ComplianceCandidateFinding {
  control_id: string;
  proposed_status: ControlStatus;
  rationale: string;
  evidence_references: CandidateEvidenceReference[];
  confidence: number | null;
}

export interface ComplianceCandidateExtractionResult {
  assessment_id: string;
  framework_id: string;
  findings: ComplianceCandidateFinding[];
  retrieved_chunk_count: number;
  evaluated_control_count: number;
  model_id: string;
  extracted_at: string;
}

export type ApprovalDecision = 'approved' | 'rejected' | 'modified';
export type ApprovalStatus =
  | 'pending'
  | 'decided'
  | 'execution_succeeded'
  | 'execution_failed';

export interface ApprovalAction {
  action_type: string;
  resource_type: string;
  resource_id: string;
  summary: string;
}

export interface ApprovalEvidenceReference {
  chunk_id: string;
  document_id: string;
}

export interface ApprovalScoreComponentBreakdown {
  scoring_version: string;
  total_controls: number;
  applicable_controls: number;
  sum_weights: string | number;
  sum_weighted_scores: string | number;
  critical_override_triggered: boolean;
}

export interface ApprovalScoreContext {
  score: string | number;
  scoring_version: string;
  component_breakdown: ApprovalScoreComponentBreakdown;
}

export interface PendingApproval {
  id: string;
  organization_id: string;
  workflow_execution_id: string;
  recommendation_id: string;
  proposed_action: ApprovalAction;
  evidence: ApprovalEvidenceReference[];
  score_context: ApprovalScoreContext | null;
  agent_trace_ids: string[];
  generator_model_id: string | null;
  reviewer_model_id: string | null;
  status: 'pending';
  created_at: string;
}

export interface DecidedApproval extends Omit<PendingApproval, 'status'> {
  status: 'decided' | 'execution_succeeded' | 'execution_failed';
  decision: ApprovalDecision;
  approver_id: string;
  decided_at: string;
  justification: string | null;
  comment: string | null;
  modified_action: ApprovalAction | null;
}

export interface ApprovalDecisionRequest {
  decision: ApprovalDecision;
  justification?: string | null;
  comment?: string | null;
  modified_action?: ApprovalAction | null;
}

export interface SystemConfigResponse {
  environment: string;
  llm_provider: string;
  vector_store: string;
  document_store: string;
  event_publisher: string;
  chat_model: string;
  embedding_model: string;
  feature_flags: Record<string, boolean>;
}
