"""Static security invariants for runtime-role migration 0012."""

from __future__ import annotations

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[2]
    / "migrations"
    / "versions"
    / "0012_runtime_role_privileges.py"
)
CI_WORKFLOW = Path(__file__).parents[3] / ".github" / "workflows" / "ci.yml"


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _ci_text() -> str:
    return CI_WORKFLOW.read_text(encoding="utf-8")


def test_runtime_role_migration_is_based_on_0011() -> None:
    text = _text()

    assert 'revision = "0012"' in text
    assert 'down_revision = "0011"' in text


def test_public_has_no_foundational_table_privileges() -> None:
    text = _text()

    assert "REVOKE ALL" in text
    assert "FROM PUBLIC" in text


def test_runtime_read_only_tables_include_control_plane_data() -> None:
    text = _text()

    assert (
        "GRANT SELECT\n"
        "                ON TABLE\n"
        "                    organizations,\n"
        "                    compliance_frameworks,\n"
        "                    compliance_controls,\n"
        "                    approval_audit_events\n"
        "                TO cloudguard_app"
    ) in text


def test_runtime_write_grants_match_application_operations() -> None:
    text = _text()

    assert (
        "GRANT SELECT, INSERT\n"
        "                ON TABLE\n"
        "                    evidence_references,\n"
        "                    assessment_score_snapshots,\n"
        "                    score_overrides,\n"
        "                    security_reviews\n"
        "                TO cloudguard_app"
    ) in text
    assert (
        "GRANT SELECT, INSERT, UPDATE\n"
        "                ON TABLE\n"
        "                    users,\n"
        "                    documents,\n"
        "                    document_chunks,\n"
        "                    compliance_assessments,\n"
        "                    control_assessments,\n"
        "                    approvals\n"
        "                TO cloudguard_app"
    ) in text


def test_upgrade_never_grants_destructive_or_ddl_like_table_privileges() -> None:
    upgrade = _text().split("def downgrade()", maxsplit=1)[0]

    assert "GRANT DELETE" not in upgrade
    assert "GRANT TRUNCATE" not in upgrade
    assert "GRANT REFERENCES" not in upgrade
    assert "GRANT TRIGGER" not in upgrade
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" not in upgrade


def test_runtime_privileges_are_reset_before_granting() -> None:
    text = _text()

    reset = text.index("FROM cloudguard_app;")
    read_only_grant = text.index("GRANT SELECT\n", reset)
    tenant_table_grant = text.index("GRANT SELECT, INSERT, UPDATE", reset)

    assert reset < read_only_grant < tenant_table_grant


def test_ci_does_not_override_migration_owned_table_privileges() -> None:
    text = _ci_text()

    assert "ON ALL TABLES IN SCHEMA public" not in text
    assert "ON ALL SEQUENCES IN SCHEMA public" not in text
    assert text.index("- name: Create application database role") < text.index(
        "- name: Migrate"
    )
