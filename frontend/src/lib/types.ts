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
