"""Domain enumerations shared across layers.

``StrEnum`` so these serialise to readable strings in JSON, SQL and logs rather
than to integers nobody can interpret six months later in an audit review.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Maps 1:1 to a Cognito group. See app/security/authz.py for what each can do."""

    ANALYST = "analyst"
    MANAGER = "manager"
    ADMIN = "admin"


class DocumentType(StrEnum):
    POLICY = "policy"
    AUDIT_REPORT = "audit_report"
    CONTROL_DOCUMENTATION = "control_documentation"
    FINANCIAL_REPORT = "financial_report"
    INVOICE = "invoice"
    ERP_EXPORT = "erp_export"
    SOP = "sop"
    RISK_REPORT = "risk_report"
    VENDOR_DOCUMENT = "vendor_document"
    CONTRACT = "contract"
    SECURITY_POLICY = "security_policy"
    UNKNOWN = "unknown"


class ConfidentialityLevel(StrEnum):
    """Least to most restricted. A retrieval filter, not a display filter."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class ProcessingStatus(StrEnum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class RiskClassification(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class InvestigationStatus(StrEnum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    ACTION_REQUIRED = "action_required"
    REMEDIATION_IN_PROGRESS = "remediation_in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
