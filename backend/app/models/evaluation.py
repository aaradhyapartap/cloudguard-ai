"""Domain and API models for Phase 9 evaluation harness and analytics."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ConfidentialityLevel, DocumentType, Role


class EvaluationCategory(StrEnum):
    """Scenario categories in the versioned golden dataset."""

    ANSWERABLE = "answerable"
    UNANSWERABLE = "unanswerable"
    AMBIGUOUS = "ambiguous"
    CROSS_DOCUMENT = "cross_document"
    UNAUTHORIZED = "unauthorized"
    PROMPT_INJECTION = "prompt_injection"


class EvaluationExecutionMode(StrEnum):
    """Execution provenance of an evaluation run.

    ``deterministic_fixture`` uses in-memory vector search and deterministic
    fixture embedding/generation adapters for fast offline and CI verification.
    ``local_pgvector_fixture`` executes vector search against real PostgreSQL +
    pgvector under ``cloudguard_app`` (``NOBYPASSRLS``) while still using
    deterministic fixture embeddings/generation.
    ``live_bedrock`` executes against real Amazon Bedrock models and pgvector.
    """

    DETERMINISTIC_FIXTURE = "deterministic_fixture"
    LOCAL_PGVECTOR_FIXTURE = "local_pgvector_fixture"
    LIVE_BEDROCK = "live_bedrock"


class SyntheticCorpusChunk(BaseModel):
    """A single deterministic chunk in the synthetic evaluation corpus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: UUID
    chunk_index: int = Field(ge=0)
    section: str = Field(min_length=1, max_length=200)
    page_number: int = Field(ge=1)
    content: str = Field(min_length=10, max_length=4000)


class SyntheticCorpusDocument(BaseModel):
    """A tenant-scoped synthetic compliance document for reproducible evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    organization_id: UUID
    uploader_id: UUID
    filename: str = Field(min_length=1, max_length=256)
    document_type: DocumentType
    confidentiality_level: ConfidentialityLevel
    department: str = Field(min_length=1, max_length=120)
    chunks: tuple[SyntheticCorpusChunk, ...] = Field(min_length=1)


class GoldenDatasetCase(BaseModel):
    """One versioned evaluation case from ``evals/golden_dataset.jsonl``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9-]+$")
    dataset_version: str = Field(min_length=1, max_length=40)
    category: EvaluationCategory
    organization_id: UUID
    caller_user_id: UUID
    caller_role: Role
    caller_email: str = Field(min_length=3, max_length=320)
    question: str = Field(min_length=5, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    expected_document_ids: tuple[UUID, ...] = ()
    expected_chunk_ids: tuple[UUID, ...] = ()
    forbidden_document_ids: tuple[UUID, ...] = ()
    forbidden_chunk_ids: tuple[UUID, ...] = ()
    required_facts: tuple[str, ...] = ()
    expect_abstention: bool = False
    expect_authorization_denial: bool = False
    run_agent_workflow: bool = False
    adversarial_case_id: str | None = Field(default=None, max_length=80)
    notes: str = Field(default="", max_length=500)


class AdversarialCorpusCase(BaseModel):
    """One adversarial prompt-injection case from ``evals/adversarial.jsonl``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9-]+$")
    payload: str = Field(min_length=5, max_length=2000)
    target_schema: str = Field(pattern=r"^(research|compliance|risk|reviewer)$")
    attempted_output: dict[str, object]


class EvaluationCaseResult(BaseModel):
    """Sanitized per-case evaluation outcome persisted with an EvaluationRun."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    category: EvaluationCategory
    passed: bool
    retrieved_chunk_ids: tuple[UUID, ...] = ()
    cited_chunk_ids: tuple[UUID, ...] = ()
    abstained: bool = False
    precision_at_5: float | None = Field(default=None, ge=0.0, le=1.0)
    recall_at_10: float | None = Field(default=None, ge=0.0, le=1.0)
    context_relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    groundedness_score: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    hallucinated: bool = False
    unsafe_response: bool = False
    cross_tenant_leak_detected: bool = False
    unauthorized_disclosure_detected: bool = False
    prompt_injection_breach_detected: bool = False
    agent_workflow_attempted: bool = False
    agent_workflow_completed: bool = False
    tool_calls_total: int = Field(default=0, ge=0)
    tool_calls_succeeded: int = Field(default=0, ge=0)
    retrieval_latency_ms: float = Field(ge=0.0)
    end_to_end_latency_ms: float = Field(ge=0.0)
    failure_reason: str | None = Field(default=None, max_length=240)


class CategoryEvaluationSummary(BaseModel):
    """Aggregated evaluation outcome for a single scenario category."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: EvaluationCategory
    case_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    avg_precision_at_5: float | None = Field(default=None, ge=0.0, le=1.0)
    avg_recall_at_10: float | None = Field(default=None, ge=0.0, le=1.0)
    avg_groundedness_score: float | None = Field(default=None, ge=0.0, le=1.0)
    avg_answer_relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    avg_citation_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    hallucination_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    unsafe_response_rate: float = Field(ge=0.0, le=1.0)


class EvaluationDenominators(BaseModel):
    """Explicit denominators for each reported evaluation metric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_cases: int = Field(ge=0)
    retrieval_evaluated_cases: int = Field(ge=0)
    citation_evaluated_cases: int = Field(ge=0)
    groundedness_evaluated_cases: int = Field(ge=0)
    answer_evaluated_cases: int = Field(ge=0)
    abstention_evaluated_cases: int = Field(ge=0)
    unauthorized_evaluated_cases: int = Field(ge=0)
    prompt_injection_evaluated_cases: int = Field(ge=0)
    agent_evaluated_cases: int = Field(ge=0)
    tool_call_evaluated_count: int = Field(ge=0)


class EvaluationMetricsSummary(BaseModel):
    """Complete structured metrics payload for an EvaluationRun."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    precision_at_5: float = Field(ge=0.0, le=1.0)
    recall_at_10: float = Field(ge=0.0, le=1.0)
    context_relevance_score: float = Field(ge=0.0, le=1.0)
    groundedness_score: float = Field(ge=0.0, le=1.0)
    answer_relevance_score: float = Field(ge=0.0, le=1.0)
    citation_accuracy: float = Field(ge=0.0, le=1.0)
    hallucination_rate: float = Field(ge=0.0, le=1.0)
    unsafe_response_rate: float = Field(ge=0.0, le=1.0)
    unanswerable_abstention_rate: float = Field(ge=0.0, le=1.0)
    cross_tenant_leak_count: int = Field(ge=0)
    unauthorized_disclosure_count: int = Field(ge=0)
    prompt_injection_breach_count: int = Field(ge=0)
    adversarial_corpus_pass_rate: float = Field(ge=0.0, le=1.0)
    tool_call_success_rate: float = Field(ge=0.0, le=1.0)
    agent_task_completion_rate: float = Field(ge=0.0, le=1.0)
    retrieval_latency_p50_ms: float = Field(ge=0.0)
    retrieval_latency_p95_ms: float = Field(ge=0.0)
    end_to_end_latency_p50_ms: float = Field(ge=0.0)
    end_to_end_latency_p95_ms: float = Field(ge=0.0)
    cost_per_1000_queries_usd: float | None = Field(default=None, ge=0.0)
    denominators: EvaluationDenominators


class TriggerEvaluationRequest(BaseModel):
    """Request body for triggering a reproducible tenant evaluation run."""

    model_config = ConfigDict(extra="forbid")

    execution_mode: Literal[EvaluationExecutionMode.DETERMINISTIC_FIXTURE] = Field(
        default=EvaluationExecutionMode.DETERMINISTIC_FIXTURE,
        description=(
            "Only deterministic_fixture (in-memory, no tenant data writes) may be "
            "triggered over HTTP. local_pgvector_fixture seeds the fixed synthetic "
            "ACME/GLOBEX corpus into PostgreSQL and is therefore restricted to the "
            "operator CLI (scripts/run_evals.py) and CI; live_bedrock is never "
            "API-triggerable."
        ),
    )
    categories: list[EvaluationCategory] | None = Field(
        default=None,
        min_length=1,
        max_length=6,
    )
    max_cases: int | None = Field(default=None, ge=1, le=50)


class EvaluationRunCreate(BaseModel):
    """Internal command model for persisting a completed EvaluationRun."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    organization_id: UUID
    triggered_by: UUID
    dataset_version: str = Field(min_length=1, max_length=64)
    execution_mode: EvaluationExecutionMode
    embedding_model_id: str = Field(min_length=1, max_length=256)
    generation_model_id: str = Field(min_length=1, max_length=256)
    reviewer_model_id: str = Field(min_length=1, max_length=256)
    judge_model_id: str = Field(min_length=1, max_length=256)
    security_gate_passed: bool
    quality_gate_advisory_passed: bool
    metrics_summary: EvaluationMetricsSummary
    category_breakdown: tuple[CategoryEvaluationSummary, ...]
    case_results: tuple[EvaluationCaseResult, ...]
    provenance_notes: tuple[str, ...]
    started_at: datetime
    completed_at: datetime


class EvaluationRunSummaryRead(BaseModel):
    """Summary projection of an EvaluationRun for list views."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    organization_id: UUID
    triggered_by: UUID
    dataset_version: str
    execution_mode: EvaluationExecutionMode
    embedding_model_id: str
    generation_model_id: str
    reviewer_model_id: str
    judge_model_id: str
    security_gate_passed: bool
    quality_gate_advisory_passed: bool
    metrics_summary: EvaluationMetricsSummary
    category_breakdown: tuple[CategoryEvaluationSummary, ...]
    provenance_notes: tuple[str, ...]
    started_at: datetime
    completed_at: datetime
    created_at: datetime


class EvaluationRunRead(EvaluationRunSummaryRead):
    """Detailed projection of an EvaluationRun including per-case results."""

    case_results: tuple[EvaluationCaseResult, ...]


class EvaluationRunListResponse(BaseModel):
    """Paginated list of tenant-scoped evaluation runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[EvaluationRunSummaryRead, ...]
    total: int = Field(ge=0)
    visibility_scope: Literal["own", "team"]


class EvaluationDatasetMetadataResponse(BaseModel):
    """Metadata describing the versioned golden dataset and adversarial corpus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_version: str
    golden_case_count: int = Field(ge=0)
    adversarial_case_count: int = Field(ge=0)
    category_counts: dict[str, int]
    metric_definitions: dict[str, str]
    known_limitations: tuple[str, ...]
