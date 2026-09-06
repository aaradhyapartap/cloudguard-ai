"""Static security invariants for Phase 7.5 migration 0011."""

from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "0011_approval_execution_audit.py"
)


def _text() -> str:
    return MIGRATION.read_text(
        encoding="utf-8"
    )


def test_execution_migration_is_based_on_0010() -> None:
    text = _text()

    assert 'revision = "0011"' in text
    assert 'down_revision = "0010"' in text


def test_execution_side_effect_is_idempotent_and_tenant_scoped() -> None:
    text = _text()

    assert "security_reviews" in text
    assert "uq_security_reviews_approval_id" in text
    assert "uq_security_reviews_idempotency_key" in text
    assert "ENABLE ROW LEVEL SECURITY" in text
    assert "FORCE ROW LEVEL SECURITY" in text


def test_audit_ledger_is_append_only() -> None:
    text = _text()

    assert "approval_audit_events" in text
    assert "guard_approval_audit_events_immutable" in text
    assert "BEFORE UPDATE OR DELETE" in text


def test_audit_payload_never_references_task_token() -> None:
    text = _text()

    assert "task_token" not in text
    assert "taskToken" not in text


def test_execution_requires_delivered_callback() -> None:
    text = _text()

    assert (
        "approval execution requires delivered callback"
        in text
    )


def test_terminal_execution_has_durable_idempotency_key() -> None:
    text = _text()

    assert "execution_idempotency_key" in text
    assert "uq_approvals_execution_idempotency_key" in text

def test_audit_trigger_is_trusted_and_runtime_cannot_forge_events() -> None:
    text = _text()

    assert "SECURITY DEFINER" in text
    assert (
        "GRANT SELECT\n"
        "                ON TABLE approval_audit_events"
        in text
    )
    assert (
        "REVOKE INSERT, UPDATE, DELETE\n"
        "                ON TABLE approval_audit_events"
        in text
    )
    assert (
        "GRANT SELECT, INSERT\n"
        "                ON TABLE security_reviews"
        in text
    )


def test_runtime_has_no_mutation_privilege_on_audit_sequence() -> None:
    text = _text()

    assert "approval_audit_events_id_seq" in text
    assert (
        "REVOKE ALL\n"
        "                ON SEQUENCE approval_audit_events_id_seq"
        in text
    )

def test_rejected_approval_cannot_enter_execution_terminal_state() -> None:
    text = _text()

    assert (
        "OLD.decision NOT IN ('approved', 'modified')"
        in text
    )
    assert (
        "rejected approval cannot enter execution state"
        in text
    )


def test_success_requires_exact_durable_side_effect() -> None:
    text = _text()

    assert "FROM security_reviews AS review" in text
    assert (
        "review.idempotency_key ="
        in text
    )
    assert (
        "NEW.execution_reference ="
        in text
    )
    assert (
        "durable approved side effect"
        in text
    )

def test_security_review_insert_requires_human_authority() -> None:
    text = _text()

    assert "guard_security_review_insert" in text
    assert "guard_security_review_insert_trigger" in text
    assert "BEFORE INSERT ON security_reviews" in text
    assert "approval_status_value <> 'decided'" in text
    assert "approval_callback_time IS NULL" in text
    assert (
        "approval_decision_value NOT IN ("
        in text
    )


def test_security_review_insert_enforces_demo_allowlist() -> None:
    text = _text()

    assert (
        "effective_action ->> 'action_type'"
        in text
    )
    assert "'create_security_review'" in text
    assert (
        "effective_action ->> 'resource_type'"
        in text
    )
    assert "'workflow_execution'" in text
    assert (
        "effective_action ->> 'resource_id'"
        in text
    )
    assert (
        "security review does not match the exact "
        in text
    )


def test_security_review_insert_guard_is_removed_on_downgrade() -> None:
    text = _text()
    downgrade = text.split(
        "def downgrade() -> None:",
        maxsplit=1,
    )[1]

    assert "DROP TRIGGER IF EXISTS " in downgrade
    assert "guard_security_review_insert_trigger " in downgrade
    assert (
        "DROP FUNCTION IF EXISTS guard_security_review_insert()"
        in downgrade
    )
