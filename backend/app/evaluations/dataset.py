"""Synthetic evaluation corpus and versioned golden/adversarial dataset loaders."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from app.adapters.local.directory import ACME, GLOBEX
from app.models.enums import ConfidentialityLevel, DocumentType
from app.models.evaluation import (
    AdversarialCorpusCase,
    GoldenDatasetCase,
    SyntheticCorpusChunk,
    SyntheticCorpusDocument,
)

DATASET_VERSION = "1.0.0"

ACME_ANALYST_ID = UUID("33333333-3333-4333-8333-333333333333")
ACME_MANAGER_ID = UUID("44444444-4444-4444-8444-444444444444")
ACME_ADMIN_ID = UUID("55555555-5555-4555-8555-555555555555")
GLOBEX_ANALYST_ID = UUID("66666666-6666-4666-8666-666666666666")

DOC_ACME_ACCESS_POLICY = UUID("a0000001-0000-4000-8000-000000000001")
DOC_ACME_DATA_RETENTION = UUID("a0000002-0000-4000-8000-000000000002")
DOC_ACME_INCIDENT_VENDOR = UUID("a0000003-0000-4000-8000-000000000003")
DOC_ACME_AUDIT_FINDINGS = UUID("a0000004-0000-4000-8000-000000000004")
DOC_ACME_CONFIDENTIAL_RISK = UUID("a0000005-0000-4000-8000-000000000005")
DOC_ACME_RESTRICTED_BOARD = UUID("a0000006-0000-4000-8000-000000000006")
DOC_GLOBEX_ACCESS_POLICY = UUID("b0000001-0000-4000-8000-000000000001")

CHUNK_AC2_ACCESS_REVIEW = UUID("c0000001-0000-4000-8000-000000000101")
CHUNK_IA2_MFA_POLICY = UUID("c0000001-0000-4000-8000-000000000102")
CHUNK_BREAK_GLASS = UUID("c0000001-0000-4000-8000-000000000103")

CHUNK_SC28_ENCRYPTION = UUID("c0000002-0000-4000-8000-000000000201")
CHUNK_AU11_AUDIT_RETENTION = UUID("c0000002-0000-4000-8000-000000000202")
CHUNK_CONTRACT_SESSION_RETENTION = UUID("c0000002-0000-4000-8000-000000000203")

CHUNK_IR4_INCIDENT_RESPONSE = UUID("c0000003-0000-4000-8000-000000000301")
CHUNK_V104_VENDOR_ACCESS = UUID("c0000003-0000-4000-8000-000000000302")
CHUNK_CP9_BACKUP_DR = UUID("c0000003-0000-4000-8000-000000000303")

CHUNK_FINDING_F2026_01 = UUID("c0000004-0000-4000-8000-000000000401")
CHUNK_FINDING_F2026_02 = UUID("c0000004-0000-4000-8000-000000000402")
CHUNK_FINDING_F2026_03 = UUID("c0000004-0000-4000-8000-000000000403")

CHUNK_CONFIDENTIAL_R401 = UUID("c0000005-0000-4000-8000-000000000501")
CHUNK_RESTRICTED_B900 = UUID("c0000006-0000-4000-8000-000000000601")

CHUNK_GLOBEX_GX_AC01 = UUID("d0000001-0000-4000-8000-000000000101")
CHUNK_GLOBEX_RETENTION = UUID("d0000001-0000-4000-8000-000000000102")

SYNTHETIC_EVALUATION_CORPUS: tuple[SyntheticCorpusDocument, ...] = (
    SyntheticCorpusDocument(
        document_id=DOC_ACME_ACCESS_POLICY,
        organization_id=ACME,
        uploader_id=ACME_ANALYST_ID,
        filename="acme-access-control-policy-v2.pdf",
        document_type=DocumentType.POLICY,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        department="Security",
        chunks=(
            SyntheticCorpusChunk(
                chunk_id=CHUNK_AC2_ACCESS_REVIEW,
                chunk_index=0,
                section="4.1 Account Provisioning and AC-2",
                page_number=4,
                content=(
                    "Control AC-2 requires that all privileged IAM accounts undergo "
                    "quarterly access reviews every 90 days by the system owner, and "
                    "dormant accounts inactive for more than 45 days must be "
                    "automatically disabled."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_IA2_MFA_POLICY,
                chunk_index=1,
                section="4.2 Multi-Factor Authentication and IA-2",
                page_number=5,
                content=(
                    "Control IA-2 mandates hardware-backed FIDO2 WebAuthn multi-factor "
                    "authentication for all production administrative sessions and VPN "
                    "access. SMS and voice one-time codes are prohibited."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_BREAK_GLASS,
                chunk_index=2,
                section="4.3 Emergency Break-Glass Exceptions",
                page_number=6,
                content=(
                    "Emergency break-glass access tokens expire after 4 hours and "
                    "require dual approval from the Security Operations Lead and the "
                    "on-call Engineering Director within 24 hours of invocation."
                ),
            ),
        ),
    ),
    SyntheticCorpusDocument(
        document_id=DOC_ACME_DATA_RETENTION,
        organization_id=ACME,
        uploader_id=ACME_ANALYST_ID,
        filename="acme-data-retention-and-crypto-standard.pdf",
        document_type=DocumentType.POLICY,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        department="Compliance",
        chunks=(
            SyntheticCorpusChunk(
                chunk_id=CHUNK_SC28_ENCRYPTION,
                chunk_index=0,
                section="2.1 Encryption at Rest and SC-28",
                page_number=2,
                content=(
                    "Control SC-28 requires AES-256-GCM encryption at rest using "
                    "customer-managed KMS keys (CMKs) with automatic annual key "
                    "rotation every 365 days and TLS 1.3 for all data in transit."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_AU11_AUDIT_RETENTION,
                chunk_index=1,
                section="3.1 Audit Log Retention and AU-11",
                page_number=7,
                content=(
                    "Control AU-11 mandates that immutable security and approval audit "
                    "logs are retained for 7 years (2555 days) in WORM-protected "
                    "storage, with hot query availability for the most recent 365 days."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_CONTRACT_SESSION_RETENTION,
                chunk_index=2,
                section="3.2 Contract and Session Retention",
                page_number=8,
                content=(
                    "Customer commercial contracts are retained for 5 years after "
                    "termination, whereas interactive application session records and "
                    "temporary API tokens are purged after 30 days."
                ),
            ),
        ),
    ),
    SyntheticCorpusDocument(
        document_id=DOC_ACME_INCIDENT_VENDOR,
        organization_id=ACME,
        uploader_id=ACME_ANALYST_ID,
        filename="acme-incident-and-vendor-governance.pdf",
        document_type=DocumentType.SOP,
        confidentiality_level=ConfidentialityLevel.PUBLIC,
        department="Operations",
        chunks=(
            SyntheticCorpusChunk(
                chunk_id=CHUNK_IR4_INCIDENT_RESPONSE,
                chunk_index=0,
                section="1.1 Incident Reporting and IR-4",
                page_number=1,
                content=(
                    "Control IR-4 requires critical severity-1 security incidents to be "
                    "escalated to the Chief Information Security Officer within 1 hour "
                    "of detection and reported to external regulators within 72 hours."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_V104_VENDOR_ACCESS,
                chunk_index=1,
                section="2.1 Third-Party Vendor Access and V-104",
                page_number=3,
                content=(
                    "Control V-104 and ISO 27001 A.9.2.5 require third-party vendor "
                    "accounts to use scoped just-in-time session grants capped at "
                    "8 hours, with mandatory SOC 2 Type II attestation renewed every "
                    "12 months."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_CP9_BACKUP_DR,
                chunk_index=2,
                section="3.1 Backup and Disaster Recovery CP-9",
                page_number=5,
                content=(
                    "Control CP-9 mandates daily incremental backups, weekly encrypted "
                    "full snapshots replicated cross-region, and quarterly restoration "
                    "drills verifying a Recovery Time Objective (RTO) of 4 hours and "
                    "Recovery Point Objective (RPO) of 15 minutes."
                ),
            ),
        ),
    ),
    SyntheticCorpusDocument(
        document_id=DOC_ACME_AUDIT_FINDINGS,
        organization_id=ACME,
        uploader_id=ACME_MANAGER_ID,
        filename="acme-q3-compliance-audit-findings.pdf",
        document_type=DocumentType.AUDIT_REPORT,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        department="Audit",
        chunks=(
            SyntheticCorpusChunk(
                chunk_id=CHUNK_FINDING_F2026_01,
                chunk_index=0,
                section="Finding F-2026-01 Access Review Lag",
                page_number=2,
                content=(
                    "Finding F-2026-01: During the Q3 internal audit, 14 privileged IAM "
                    "accounts in the Finance analytics environment had elapsed 112 days "
                    "without a completed AC-2 access review, exceeding the 90-day "
                    "policy ceiling by 22 days."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_FINDING_F2026_02,
                chunk_index=1,
                section="Finding F-2026-02 Vendor Session Scope",
                page_number=4,
                content=(
                    "Finding F-2026-02: Three third-party telemetry maintenance "
                    "contractors under control V-104 retained persistent 30-day VPN "
                    "credentials instead of the mandated 8-hour just-in-time session "
                    "grants."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_FINDING_F2026_03,
                chunk_index=2,
                section="Finding F-2026-03 DR Drill Verification",
                page_number=6,
                content=(
                    "Finding F-2026-03: The Q3 disaster recovery restoration drill "
                    "achieved a 2-hour 40-minute RTO and 10-minute RPO across all "
                    "primary PostgreSQL clusters, satisfying CP-9 targets."
                ),
            ),
        ),
    ),
    SyntheticCorpusDocument(
        document_id=DOC_ACME_CONFIDENTIAL_RISK,
        organization_id=ACME,
        uploader_id=ACME_MANAGER_ID,
        filename="acme-confidential-risk-register.pdf",
        document_type=DocumentType.RISK_REPORT,
        confidentiality_level=ConfidentialityLevel.CONFIDENTIAL,
        department="Audit",
        chunks=(
            SyntheticCorpusChunk(
                chunk_id=CHUNK_CONFIDENTIAL_R401,
                chunk_index=0,
                section="Risk R-401 Merger Integration Exposure",
                page_number=1,
                content=(
                    "Confidential Manager-level risk assessment R-401: The pending "
                    "Project Atlas subsidiary integration introduces 6 legacy ERP "
                    "databases lacking automated SC-28 CMK rotation; remediation "
                    "deadline is 60 days post-close with Compliance Manager sign-off."
                ),
            ),
        ),
    ),
    SyntheticCorpusDocument(
        document_id=DOC_ACME_RESTRICTED_BOARD,
        organization_id=ACME,
        uploader_id=ACME_ADMIN_ID,
        filename="acme-restricted-board-forensics.pdf",
        document_type=DocumentType.CONTROL_DOCUMENTATION,
        confidentiality_level=ConfidentialityLevel.RESTRICTED,
        department="IT",
        chunks=(
            SyntheticCorpusChunk(
                chunk_id=CHUNK_RESTRICTED_B900,
                chunk_index=0,
                section="Board Charter and Restricted Forensics B-900",
                page_number=1,
                content=(
                    "Restricted Admin-clearance forensic dossier B-900: Legal counsel "
                    "privileged investigation into cold-storage root key ceremony "
                    "hardware serials HSM-ACME-9941 and HSM-ACME-9942."
                ),
            ),
        ),
    ),
    SyntheticCorpusDocument(
        document_id=DOC_GLOBEX_ACCESS_POLICY,
        organization_id=GLOBEX,
        uploader_id=GLOBEX_ANALYST_ID,
        filename="globex-industrial-security-policy.pdf",
        document_type=DocumentType.POLICY,
        confidentiality_level=ConfidentialityLevel.INTERNAL,
        department="Ops",
        chunks=(
            SyntheticCorpusChunk(
                chunk_id=CHUNK_GLOBEX_GX_AC01,
                chunk_index=0,
                section="1.1 Globex Access Reviews GX-AC-01",
                page_number=1,
                content=(
                    "Globex Industrial control GX-AC-01 mandates semi-annual access "
                    "reviews every 180 days for plant SCADA operators and disables "
                    "inactive accounts after 60 days."
                ),
            ),
            SyntheticCorpusChunk(
                chunk_id=CHUNK_GLOBEX_RETENTION,
                chunk_index=1,
                section="1.2 Globex Backup and Retention",
                page_number=2,
                content=(
                    "Globex Industrial retains operational telemetry logs for 3 years "
                    "(1095 days) in regional object storage with a 12-hour RTO target "
                    "for manufacturing execution systems."
                ),
            ),
        ),
    ),
)


def repo_root() -> Path:
    """Resolve the repository root directory from ``backend/app/evaluations``."""
    return Path(__file__).resolve().parents[3]


def default_golden_dataset_path() -> Path:
    return repo_root() / "evals" / "golden_dataset.jsonl"


def default_adversarial_dataset_path() -> Path:
    return repo_root() / "evals" / "adversarial.jsonl"


def security_fixture_corpus_path() -> Path:
    return (
        repo_root()
        / "backend"
        / "tests"
        / "fixtures"
        / "security"
        / "prompt_injection.json"
    )


def load_golden_dataset(
    path: Path | None = None,
) -> tuple[GoldenDatasetCase, ...]:
    """Load and validate all golden evaluation cases from JSONL."""
    target = path or default_golden_dataset_path()
    cases: list[GoldenDatasetCase] = []
    seen_ids: set[str] = set()
    for line_no, raw_line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        case = GoldenDatasetCase.model_validate(payload)
        if case.case_id in seen_ids:
            raise ValueError(
                f"Duplicate golden dataset case_id {case.case_id!r} at line {line_no}"
            )
        seen_ids.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def load_adversarial_corpus(
    path: Path | None = None,
) -> tuple[AdversarialCorpusCase, ...]:
    """Load and validate the reusable adversarial prompt-injection corpus."""
    target = path or default_adversarial_dataset_path()
    cases: list[AdversarialCorpusCase] = []
    seen_ids: set[str] = set()
    for line_no, raw_line in enumerate(
        target.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw_line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        case = AdversarialCorpusCase.model_validate(payload)
        if case.id in seen_ids:
            raise ValueError(
                f"Duplicate adversarial case id {case.id!r} at line {line_no}"
            )
        seen_ids.add(case.id)
        cases.append(case)
    return tuple(cases)
