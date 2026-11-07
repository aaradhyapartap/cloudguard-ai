"""Mocked regressions for deployment CI verification."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from verify_deployment_ci import fetch_check_runs_via_gh, verify_deployment_ci

TEST_SHA = "a" * 40
WRONG_SHA = "b" * 40
TEST_REPO = "aaradhyapartap/cloudguard-ai"


def _build_check_run(
    name: str,
    status: str = "completed",
    conclusion: str = "success",
    run_id: int = 1,
    sha: str = TEST_SHA,
    workflow_path: str = ".github/workflows/ci.yml",
    workflow_run_id: int = 100,
    app_slug: str = "github-actions",
    repository: str | None = TEST_REPO,
) -> dict[str, object]:
    cr: dict[str, object] = {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "head_sha": sha,
        "workflow_path": workflow_path,
        "workflow_run_id": workflow_run_id,
        "app": {"slug": app_slug, "name": "GitHub Actions"},
    }
    if repository:
        cr["repository"] = {"full_name": repository}
    return cr


def _build_valid_ci_suite(sha: str = TEST_SHA) -> list[dict[str, object]]:
    return [
        _build_check_run("Backend", sha=sha),
        _build_check_run("Infrastructure", sha=sha),
        _build_check_run("Frontend", sha=sha),
        _build_check_run("Security", sha=sha),
    ]


# ============================================================================
# Basic check run validation tests
# ============================================================================


def test_successful_ci() -> None:
    """All required jobs present, completed, and successful must pass."""
    suite = _build_valid_ci_suite()
    verify_deployment_ci(suite, TEST_SHA)


def test_self_running_deployment_is_excluded() -> None:
    """Deployment checks (CDK diff, Preflight Safety Gate) must be excluded from verification."""
    suite = _build_valid_ci_suite()
    suite.extend([
        _build_check_run("Preflight Safety Gate", status="in_progress", conclusion=""),
        _build_check_run("CDK diff", status="completed", conclusion="success"),
        _build_check_run("CDK deploy", status="queued", conclusion=""),
    ])
    verify_deployment_ci(suite, TEST_SHA)


def test_missing_required_job() -> None:
    """Missing any required job must raise ValueError."""
    suite = [
        _build_check_run("Backend"),
        _build_check_run("Infrastructure"),
        _build_check_run("Security"),
        # Frontend is missing!
    ]
    with pytest.raises(ValueError, match="Missing required CI job 'Frontend'"):
        verify_deployment_ci(suite, TEST_SHA)


def test_skipped_job_blocks() -> None:
    """A skipped required job must block deployment."""
    suite = [
        _build_check_run("Backend"),
        _build_check_run("Infrastructure", conclusion="skipped"),
        _build_check_run("Frontend"),
        _build_check_run("Security"),
    ]
    with pytest.raises(ValueError, match="did not succeed \\(conclusion='skipped'\\)"):
        verify_deployment_ci(suite, TEST_SHA)


def test_neutral_job_blocks() -> None:
    """A neutral required job must block deployment."""
    suite = [
        _build_check_run("Backend"),
        _build_check_run("Infrastructure"),
        _build_check_run("Frontend"),
        _build_check_run("Security", conclusion="neutral"),
    ]
    with pytest.raises(ValueError, match="did not succeed \\(conclusion='neutral'\\)"):
        verify_deployment_ci(suite, TEST_SHA)


def test_failed_job_blocks() -> None:
    """A failed required job must block deployment."""
    suite = [
        _build_check_run("Backend", conclusion="failure"),
        _build_check_run("Infrastructure"),
        _build_check_run("Frontend"),
        _build_check_run("Security"),
    ]
    with pytest.raises(ValueError, match="did not succeed \\(conclusion='failure'\\)"):
        verify_deployment_ci(suite, TEST_SHA)


def test_incomplete_job_blocks() -> None:
    """An incomplete required job must block deployment."""
    suite = [
        _build_check_run("Backend", status="in_progress", conclusion=""),
        _build_check_run("Infrastructure"),
        _build_check_run("Frontend"),
        _build_check_run("Security"),
    ]
    with pytest.raises(ValueError, match="is not completed \\(status='in_progress'\\)"):
        verify_deployment_ci(suite, TEST_SHA)


def test_wrong_sha_blocks() -> None:
    """Check runs matching a different SHA do not satisfy the deployment commit."""
    suite = _build_valid_ci_suite(sha=WRONG_SHA)
    msg = f"No trusted CI workflow runs for .github/workflows/ci.yml found on commit {TEST_SHA}"
    with pytest.raises(ValueError, match=re.escape(msg)):
        verify_deployment_ci(suite, TEST_SHA)


def test_invalid_sha_format() -> None:
    """Malformed commit SHA must raise ValueError."""
    suite = _build_valid_ci_suite()
    with pytest.raises(ValueError, match="Invalid deployment commit SHA: 'bad-sha'"):
        verify_deployment_ci(suite, "bad-sha")


def test_reruns_handled_deterministically() -> None:
    """Reruns must deterministically use the latest run by ID."""
    suite = [
        _build_check_run("Backend", run_id=1),
        _build_check_run("Infrastructure", run_id=2),
        _build_check_run("Frontend", run_id=3),
        # Security first failed (id=4), then succeeded on rerun (id=5)
        _build_check_run("Security", status="completed", conclusion="failure", run_id=4),
        _build_check_run("Security", status="completed", conclusion="success", run_id=5),
    ]
    # Should pass because latest Security run succeeded
    verify_deployment_ci(suite, TEST_SHA)

    # In reverse: Security first succeeded (id=6), then failed on rerun (id=7)
    suite_failing = [
        _build_check_run("Backend", run_id=1),
        _build_check_run("Infrastructure", run_id=2),
        _build_check_run("Frontend", run_id=3),
        _build_check_run("Security", status="completed", conclusion="success", run_id=6),
        _build_check_run("Security", status="completed", conclusion="failure", run_id=7),
    ]
    with pytest.raises(ValueError, match="did not succeed \\(conclusion='failure'\\)"):
        verify_deployment_ci(suite_failing, TEST_SHA)


def test_provenance_spoofing_rejected() -> None:
    """Check runs from untrusted apps must be rejected even if job names match."""
    suite = [
        _build_check_run("Backend", app_slug="malicious-app"),  # Spoofed app!
        _build_check_run("Infrastructure"),
        _build_check_run("Frontend"),
        _build_check_run("Security"),
    ]
    with pytest.raises(ValueError, match="Missing required CI job 'Backend'"):
        verify_deployment_ci(suite, TEST_SHA)


def test_wrong_workflow_rejected() -> None:
    """Check runs from other workflows must be rejected even if job names match."""
    suite = [
        _build_check_run(
            "Backend",
            workflow_path=".github/workflows/nightly-eval.yml",  # Wrong workflow!
        ),
        _build_check_run("Infrastructure"),
        _build_check_run("Frontend"),
        _build_check_run("Security"),
    ]
    with pytest.raises(ValueError, match="Missing required CI job 'Backend'"):
        verify_deployment_ci(suite, TEST_SHA)


def test_split_jobs_across_different_runs_rejected() -> None:
    """Required jobs must all belong to the same trusted run, not scattered across runs."""
    suite = [
        # Run 101 has Backend and Infrastructure
        _build_check_run("Backend", workflow_run_id=101, run_id=1),
        _build_check_run("Infrastructure", workflow_run_id=101, run_id=2),
        # Run 102 has Frontend and Security
        _build_check_run("Frontend", workflow_run_id=102, run_id=3),
        _build_check_run("Security", workflow_run_id=102, run_id=4),
    ]
    # Selected run (102) is missing Backend
    with pytest.raises(
        ValueError,
        match="Missing required CI job 'Backend' in trusted workflow run 102",
    ):
        verify_deployment_ci(suite, TEST_SHA)


def test_no_trusted_ci_runs_raises() -> None:
    """If no check runs match the trusted workflow and app, verification must fail."""
    suite = [
        _build_check_run("Backend", workflow_path=".github/workflows/nightly.yml"),
        _build_check_run("Security", app_slug="third-party-bot"),
    ]
    msg = f"No trusted CI workflow runs for .github/workflows/ci.yml found on commit {TEST_SHA}"
    with pytest.raises(ValueError, match=re.escape(msg)):
        verify_deployment_ci(suite, TEST_SHA)


# ============================================================================
# Missing provenance rejection tests
# ============================================================================


def test_missing_app_provenance_rejected() -> None:
    """Check run missing 'app' provenance must raise ValueError, not pass via fixture bypass."""
    bad_item = _build_check_run("Backend")
    del bad_item["app"]
    suite = [bad_item, _build_check_run("Infrastructure"), _build_check_run("Frontend")]
    with pytest.raises(ValueError, match="missing required 'app' provenance"):
        verify_deployment_ci(suite, TEST_SHA)


def test_missing_workflow_path_provenance_rejected() -> None:
    """Check run missing 'workflow_path' must raise ValueError."""
    bad_item = _build_check_run("Backend")
    del bad_item["workflow_path"]
    suite = [bad_item, _build_check_run("Infrastructure"), _build_check_run("Frontend")]
    with pytest.raises(ValueError, match="missing required 'workflow_path' provenance"):
        verify_deployment_ci(suite, TEST_SHA)


def test_missing_head_sha_provenance_rejected() -> None:
    """Check run missing 'head_sha' must raise ValueError."""
    bad_item = _build_check_run("Backend")
    del bad_item["head_sha"]
    suite = [bad_item, _build_check_run("Infrastructure"), _build_check_run("Frontend")]
    with pytest.raises(ValueError, match="missing required 'head_sha' provenance"):
        verify_deployment_ci(suite, TEST_SHA)


def test_missing_run_identity_rejected() -> None:
    """Check run missing any run identity must raise ValueError."""
    bad_item = _build_check_run("Backend")
    del bad_item["workflow_run_id"]
    suite = [bad_item, _build_check_run("Infrastructure"), _build_check_run("Frontend")]
    with pytest.raises(ValueError, match="missing run identity"):
        verify_deployment_ci(suite, TEST_SHA)


def test_missing_expected_repo_provenance_rejected() -> None:
    """When expected_repo is enforced, items missing repo provenance must raise ValueError."""
    bad_item = _build_check_run("Backend")
    del bad_item["repository"]
    suite = [bad_item, _build_check_run("Infrastructure"), _build_check_run("Frontend")]
    with pytest.raises(ValueError, match="missing required 'repository' provenance"):
        verify_deployment_ci(suite, TEST_SHA, expected_repo=TEST_REPO)


# ============================================================================
# Mocked gh CLI / subprocess regressions for fetch_check_runs_via_gh
# ============================================================================


def _proc(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh", "api"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_gh_lookup_http_403_fails() -> None:
    """HTTP 403 or command failure on workflow runs lookup must fail closed with RuntimeError."""
    mock_run = MagicMock(
        return_value=_proc(returncode=1, stderr="HTTP 403: Resource not accessible")
    )
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match=r"Failed to query CI workflow runs .* HTTP 403"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_malformed_workflow_runs_response_fails() -> None:
    """Non-JSON response from workflow runs API must fail closed with RuntimeError."""
    mock_run = MagicMock(return_value=_proc(returncode=0, stdout="<html>Bad Gateway</html>"))
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match="Malformed response from workflow runs API"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_not_dict_workflow_runs_response_fails() -> None:
    """Workflow runs response not returning a JSON object must fail closed."""
    mock_run = MagicMock(return_value=_proc(returncode=0, stdout="[]"))
    with patch("subprocess.run", mock_run):
        with pytest.raises(TypeError, match="expected JSON object, got list"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_empty_workflow_runs_fails() -> None:
    """No matching workflow runs for target commit must fail closed with RuntimeError."""
    mock_run = MagicMock(
        return_value=_proc(returncode=0, stdout=json.dumps({"workflow_runs": []}))
    )
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match=f"No CI workflow runs found for commit {TEST_SHA}"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_selected_run_wrong_sha_fails() -> None:
    """Selected run with mismatched head_sha must fail closed."""
    wf_payload = {
        "workflow_runs": [{
            "id": 12345,
            "head_sha": WRONG_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": TEST_REPO},
        }]
    }
    mock_run = MagicMock(return_value=_proc(returncode=0, stdout=json.dumps(wf_payload)))
    with patch("subprocess.run", mock_run):
        expected_msg = f"has head_sha '{WRONG_SHA}', expected '{TEST_SHA}'"
        with pytest.raises(RuntimeError, match=re.escape(expected_msg)):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_selected_run_wrong_workflow_path_fails() -> None:
    """Selected run with mismatched workflow path must fail closed."""
    wf_payload = {
        "workflow_runs": [{
            "id": 12345,
            "head_sha": TEST_SHA,
            "path": ".github/workflows/deploy.yml",
            "repository": {"full_name": TEST_REPO},
        }]
    }
    mock_run = MagicMock(return_value=_proc(returncode=0, stdout=json.dumps(wf_payload)))
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match=r"expected '\.github/workflows/ci\.yml'"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_selected_run_wrong_repository_fails() -> None:
    """Selected run belonging to a different repository must fail closed."""
    wf_payload = {
        "workflow_runs": [{
            "id": 12345,
            "head_sha": TEST_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": "attacker-org/spoofed-repo"},
        }]
    }
    mock_run = MagicMock(return_value=_proc(returncode=0, stdout=json.dumps(wf_payload)))
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match="does not match expected"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_selected_run_missing_repository_fails() -> None:
    """Selected run missing repository provenance must fail closed."""
    wf_payload = {
        "workflow_runs": [{
            "id": 12345,
            "head_sha": TEST_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": None,
        }]
    }
    mock_run = MagicMock(return_value=_proc(returncode=0, stdout=json.dumps(wf_payload)))
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match="missing required 'repository' provenance"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_selected_run_missing_id_fails() -> None:
    """Selected run missing a valid run ID must fail closed."""
    wf_payload = {
        "workflow_runs": [{
            "id": 0,
            "head_sha": TEST_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": TEST_REPO},
        }]
    }
    mock_run = MagicMock(return_value=_proc(returncode=0, stdout=json.dumps(wf_payload)))
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match="missing a valid 'id'"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_conflicting_job_head_sha_fails() -> None:
    """API job item reporting conflicting head_sha must raise RuntimeError, not be overwritten."""
    wf_payload = {
        "workflow_runs": [{
            "id": 12345,
            "head_sha": TEST_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": TEST_REPO},
        }]
    }
    jobs_payload = {
        "jobs": [{
            "id": 101,
            "name": "Backend",
            "status": "completed",
            "conclusion": "success",
            "head_sha": "f" * 40,  # Conflicting SHA!
        }]
    }
    mock_run = MagicMock(side_effect=[
        _proc(returncode=0, stdout=json.dumps(wf_payload)),
        _proc(returncode=0, stdout=json.dumps(jobs_payload)),
    ])
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match="has conflicting head_sha"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_conflicting_job_run_id_fails() -> None:
    """API job item reporting conflicting run_id must raise RuntimeError, not be overwritten."""
    wf_payload = {
        "workflow_runs": [{
            "id": 12345,
            "head_sha": TEST_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": TEST_REPO},
        }]
    }
    jobs_payload = {
        "jobs": [{
            "id": 101,
            "name": "Backend",
            "status": "completed",
            "conclusion": "success",
            "run_id": 999999,  # Conflicting run_id!
        }]
    }
    mock_run = MagicMock(side_effect=[
        _proc(returncode=0, stdout=json.dumps(wf_payload)),
        _proc(returncode=0, stdout=json.dumps(jobs_payload)),
    ])
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match="has conflicting run_id"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_pagination_failure_fails() -> None:
    """Pagination failure on subsequent page must fail closed; never accept partial retrieval."""
    wf_payload = {
        "workflow_runs": [{
            "id": 12345,
            "head_sha": TEST_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": TEST_REPO},
        }]
    }
    # Page 1 returns 100 jobs, total_count=150
    page_1_jobs = [
        {"id": i, "name": f"job-{i}", "status": "completed", "conclusion": "success"}
        for i in range(100)
    ]
    page_1_payload = {"total_count": 150, "jobs": page_1_jobs}

    mock_run = MagicMock(side_effect=[
        _proc(returncode=0, stdout=json.dumps(wf_payload)),
        _proc(returncode=0, stdout=json.dumps(page_1_payload)),
        # Page 2 fails with 502 Bad Gateway
        _proc(returncode=1, stderr="HTTP 502: Bad Gateway"),
    ])
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match="Failed to query jobs for run 12345 \\(page 2\\)"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_empty_jobs_fails() -> None:
    """Workflow run containing zero jobs must raise RuntimeError."""
    wf_payload = {
        "workflow_runs": [{
            "id": 12345,
            "head_sha": TEST_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": TEST_REPO},
        }]
    }
    jobs_payload = {"total_count": 0, "jobs": []}
    mock_run = MagicMock(side_effect=[
        _proc(returncode=0, stdout=json.dumps(wf_payload)),
        _proc(returncode=0, stdout=json.dumps(jobs_payload)),
    ])
    with patch("subprocess.run", mock_run):
        with pytest.raises(RuntimeError, match="Workflow run 12345 contains no jobs"):
            fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)


def test_gh_realistic_successful_response() -> None:
    """Realistic workflow run and job response succeeds and passes verify_deployment_ci."""
    run_id = 987654321
    wf_payload = {
        "workflow_runs": [{
            "id": run_id,
            "name": "CI",
            "head_sha": TEST_SHA,
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": TEST_REPO},
            "status": "completed",
            "conclusion": "success",
        }]
    }
    jobs_payload = {
        "total_count": 4,
        "jobs": [
            {
                "id": 1001,
                "run_id": run_id,
                "name": "Backend",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 1002,
                "run_id": run_id,
                "name": "Infrastructure",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 1003,
                "run_id": run_id,
                "name": "Frontend",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 1004,
                "run_id": run_id,
                "name": "Security",
                "status": "completed",
                "conclusion": "success",
            },
        ],
    }
    mock_run = MagicMock(side_effect=[
        _proc(returncode=0, stdout=json.dumps(wf_payload)),
        _proc(returncode=0, stdout=json.dumps(jobs_payload)),
    ])
    with patch("subprocess.run", mock_run):
        jobs = fetch_check_runs_via_gh(TEST_REPO, TEST_SHA)

    assert len(jobs) == 4
    # Ensure enriched provenance is present
    for j in jobs:
        assert j["workflow_run_id"] == run_id
        assert j["workflow_path"] == ".github/workflows/ci.yml"
        assert j["head_sha"] == TEST_SHA
        assert j["app"]["slug"] == "github-actions"
        assert j["repository"] == TEST_REPO

    # Pass directly into verify_deployment_ci with expected_repo
    verify_deployment_ci(jobs, TEST_SHA, expected_repo=TEST_REPO)
