#!/usr/bin/env python3
"""Validates that trusted CI check runs have passed on the exact deployment commit.

Enforces that Backend, Infrastructure, Frontend, and Security all exist, are completed,
and concluded successfully for the exact target deployment commit SHA before deployment
can proceed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

REQUIRED_CI_JOBS: tuple[str, ...] = (
    "Backend",
    "Infrastructure",
    "Frontend",
    "Security",
)

DEPLOYMENT_JOB_NAMES: frozenset[str] = frozenset({
    "Preflight Safety Gate",
    "CDK diff",
    "CDK deploy",
    "Deploy",
    "preflight-safety",
    "cdk-action",
})

TRUSTED_WORKFLOW_PATHS: tuple[str, ...] = (
    ".github/workflows/ci.yml",
    "ci.yml",
)

TRUSTED_APP_SLUGS: frozenset[str] = frozenset({
    "github-actions",
})

SHA_REGEX = re.compile(r"^[0-9a-f]{40}$")


def _is_trusted_app(cr: dict[str, Any], job_name: str) -> bool:
    app = cr.get("app")
    if not app or not isinstance(app, dict):
        raise ValueError(f"Check run '{job_name}' missing required 'app' provenance.")
    slug = str(app.get("slug", "")).strip().lower()
    return slug in TRUSTED_APP_SLUGS


def _is_trusted_workflow(
    cr: dict[str, Any],
    job_name: str,
    expected_workflow: str = ".github/workflows/ci.yml",
) -> bool:
    wf_path = cr.get("workflow_path") or cr.get("workflow") or cr.get("path")
    check_suite = cr.get("check_suite")
    if isinstance(check_suite, dict):
        wf_path = wf_path or check_suite.get("workflow_path") or check_suite.get("path")
    if not wf_path:
        raise ValueError(f"Check run '{job_name}' missing required 'workflow_path' provenance.")
    norm_path = str(wf_path).strip().lower().replace("\\", "/")
    return norm_path == expected_workflow.lower()


def _extract_run_id(cr: dict[str, Any]) -> int | str | None:
    for key in ("workflow_run_id", "run_id", "check_suite_id"):
        val = cr.get(key)
        if val is not None:
            if isinstance(val, (int, str)):
                return val
            return str(val)
    check_suite = cr.get("check_suite")
    if isinstance(check_suite, dict) and check_suite.get("id") is not None:
        cs_id = check_suite["id"]
        if isinstance(cs_id, (int, str)):
            return cs_id
        return str(cs_id)
    details_url = str(cr.get("details_url", ""))
    match = re.search(r"/actions/runs/(\d+)", details_url)
    if match:
        return int(match.group(1))
    return None


def verify_deployment_ci(
    check_runs: list[dict[str, Any]],
    expected_sha: str,
    expected_repo: str | None = None,
    expected_workflow: str = ".github/workflows/ci.yml",
) -> None:
    """Validate that trusted CI check runs have passed on the exact deployment commit.

    Selects the intended workflow run for expected_sha, verifying that Backend,
    Infrastructure, Frontend, and Security all belong to that exact trusted run,
    are completed, and concluded with success.
    """
    clean_sha = expected_sha.strip()
    if not SHA_REGEX.match(clean_sha):
        raise ValueError(f"Invalid deployment commit SHA: '{clean_sha}'.")

    if not check_runs:
        raise ValueError(f"No check runs found for commit {clean_sha}.")

    # 1. Filter runs by commit SHA, trusted app, non-deployment, and trusted workflow
    trusted_ci_runs: list[dict[str, Any]] = []
    for cr in check_runs:
        if not isinstance(cr, dict):
            raise TypeError(
                f"Invalid check run payload item: expected dict, got {type(cr).__name__}."
            )

        name = str(cr.get("name", "")).strip()
        if not name:
            raise ValueError("Check run item is missing required 'name' field.")

        # Exclude self-running deployment checks
        if name in DEPLOYMENT_JOB_NAMES or name.startswith("CDK "):
            continue
        if "deploy" in name.lower() and "ci" not in name.lower():
            continue

        # Check commit SHA provenance
        cr_sha = cr.get("head_sha")
        if not cr_sha:
            raise ValueError(f"Check run '{name}' missing required 'head_sha' provenance.")
        if str(cr_sha).strip() != clean_sha:
            continue

        # Require and verify trusted app provenance (rejects missing, skips untrusted)
        if not _is_trusted_app(cr, name):
            continue

        # Require and verify trusted workflow provenance (rejects missing, skips untrusted)
        if not _is_trusted_workflow(cr, name, expected_workflow=expected_workflow):
            continue

        # Check repository provenance if provided
        if expected_repo:
            cr_repo = cr.get("repository")
            if isinstance(cr_repo, dict):
                repo_name = cr_repo.get("full_name")
            else:
                repo_name = str(cr_repo).strip() if cr_repo else None
            if not repo_name:
                raise ValueError(f"Check run '{name}' missing required 'repository' provenance.")
            if repo_name.lower() != expected_repo.lower():
                continue

        trusted_ci_runs.append(cr)

    if not trusted_ci_runs:
        raise ValueError(
            f"No trusted CI workflow runs for {expected_workflow} found on commit {clean_sha}."
        )

    # 2. Group check-runs by workflow run ID
    runs_by_id: dict[int | str, list[dict[str, Any]]] = {}
    for cr in trusted_ci_runs:
        run_id = _extract_run_id(cr)
        if run_id is None:
            raise ValueError(f"Check run '{cr.get('name')}' missing run identity.")
        runs_by_id.setdefault(run_id, []).append(cr)

    # 3. Deterministically select the latest trusted workflow run by ID / max check-run ID
    def _run_sort_key(item: tuple[int | str, list[dict[str, Any]]]) -> int:
        r_id, items = item
        if isinstance(r_id, int):
            return r_id
        job_ids = [int(x.get("id", 0) or 0) for x in items]
        return max(job_ids) if job_ids else 0

    sorted_runs = sorted(runs_by_id.items(), key=_run_sort_key, reverse=True)
    selected_run_id, selected_run_items = sorted_runs[0]

    # 4. Group jobs within the selected run, handling reruns deterministically (highest job id)
    latest_by_name: dict[str, dict[str, Any]] = {}
    for cr in selected_run_items:
        name = str(cr.get("name", "")).strip()
        existing = latest_by_name.get(name)
        if existing is None:
            latest_by_name[name] = cr
        else:
            existing_id = int(existing.get("id", 0) or 0)
            current_id = int(cr.get("id", 0) or 0)
            if current_id >= existing_id:
                latest_by_name[name] = cr

    # 5. Require all four successful jobs from the selected trusted run
    for required_job in REQUIRED_CI_JOBS:
        if required_job not in latest_by_name:
            raise ValueError(
                f"Missing required CI job '{required_job}' in trusted workflow run {selected_run_id} "
                f"for commit {clean_sha}. Available jobs in run: {list(latest_by_name.keys())}."
            )

        job_run = latest_by_name[required_job]
        status = str(job_run.get("status", "")).strip()
        conclusion = str(job_run.get("conclusion", "")).strip()

        if status != "completed":
            raise ValueError(
                f"Required CI job '{required_job}' is not completed (status='{status}')."
            )

        if conclusion != "success":
            raise ValueError(
                f"Required CI job '{required_job}' did not succeed (conclusion='{conclusion}'). "
                f"Missing, skipped, neutral, or failed jobs are not permitted."
            )


def fetch_check_runs_via_gh(repo: str, commit_sha: str) -> list[dict[str, Any]]:
    """Query GitHub check-runs API with automatic pagination using the gh CLI.

    Fails closed if trusted workflow lookup fails, returns malformed data, or finds
    no matching run. Verifies the selected run's actual repository, exact workflow
    path, head SHA, and run identity before checking its jobs.
    """
    clean_sha = commit_sha.strip()
    if not SHA_REGEX.match(clean_sha):
        raise ValueError(f"Invalid deployment commit SHA: '{clean_sha}'.")

    # 1. Query workflow runs for ci.yml on target commit
    wf_endpoint = f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={clean_sha}&per_page=10"
    wf_res = subprocess.run(
        ["gh", "api", wf_endpoint],
        capture_output=True,
        text=True,
        check=False,
    )
    if wf_res.returncode != 0:
        err = wf_res.stderr.strip() or f"exit code {wf_res.returncode}"
        raise RuntimeError(
            f"Failed to query CI workflow runs for commit {clean_sha} in {repo}: {err}"
        )

    try:
        wf_data = json.loads(wf_res.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(
            f"Malformed response from workflow runs API for {repo}: {exc}"
        ) from exc

    if not isinstance(wf_data, dict):
        raise TypeError(
            f"Malformed response from workflow runs API for {repo}: "
            f"expected JSON object, got {type(wf_data).__name__}."
        )

    runs = wf_data.get("workflow_runs")
    if not isinstance(runs, list) or not runs:
        raise RuntimeError(f"No CI workflow runs found for commit {clean_sha} in {repo}.")

    # 2. Select latest run and verify run identity, repository, path, and head SHA
    valid_runs: list[dict[str, Any]] = []
    for r in runs:
        if not isinstance(r, dict):
            raise TypeError(
                f"Malformed workflow run item: expected dict, got {type(r).__name__}."
            )
        valid_runs.append(r)

    valid_runs.sort(key=lambda r: int(r.get("id", 0) or 0), reverse=True)
    selected_run = valid_runs[0]

    run_id = selected_run.get("id")
    if not run_id or not isinstance(run_id, int) or run_id <= 0:
        raise RuntimeError(f"Selected workflow run is missing a valid 'id': {selected_run}.")

    run_sha = str(selected_run.get("head_sha", "")).strip()
    if not run_sha or run_sha != clean_sha:
        raise RuntimeError(
            f"Selected workflow run {run_id} has head_sha '{run_sha}', expected '{clean_sha}'."
        )

    run_path = str(selected_run.get("path", "")).strip().replace("\\", "/").lower()
    if run_path != ".github/workflows/ci.yml":
        raise RuntimeError(
            f"Selected workflow run {run_id} has path '{selected_run.get('path')}', "
            "expected '.github/workflows/ci.yml'."
        )

    run_repo_obj = selected_run.get("repository")
    if isinstance(run_repo_obj, dict):
        actual_repo = str(run_repo_obj.get("full_name", "")).strip()
    elif run_repo_obj is not None:
        actual_repo = str(run_repo_obj).strip()
    else:
        actual_repo = ""

    if not actual_repo:
        raise RuntimeError(
            f"Selected workflow run {run_id} missing required 'repository' provenance."
        )
    if actual_repo.lower() != repo.strip().lower():
        raise RuntimeError(
            f"Selected workflow run {run_id} repository '{actual_repo}' does not match expected '{repo}'."
        )

    # 3. Fetch jobs for the verified run with pagination; fail immediately on any error
    jobs: list[dict[str, Any]] = []
    page = 1
    per_page = 100

    while True:
        jobs_endpoint = (
            f"repos/{repo}/actions/runs/{run_id}/jobs?per_page={per_page}&page={page}"
        )
        j_res = subprocess.run(
            ["gh", "api", jobs_endpoint],
            capture_output=True,
            text=True,
            check=False,
        )
        if j_res.returncode != 0:
            err = j_res.stderr.strip() or f"exit code {j_res.returncode}"
            raise RuntimeError(
                f"Failed to query jobs for run {run_id} (page {page}): {err}"
            )

        try:
            j_data = json.loads(j_res.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError(
                f"Malformed response from jobs API for run {run_id} (page {page}): {exc}"
            ) from exc

        if not isinstance(j_data, dict):
            raise TypeError(
                f"Malformed response from jobs API for run {run_id} (page {page}): "
                f"expected JSON object, got {type(j_data).__name__}."
            )

        current_jobs = j_data.get("jobs")
        if not isinstance(current_jobs, list):
            raise TypeError(
                f"Malformed response from jobs API for run {run_id} (page {page}): "
                "'jobs' field missing or not a list."
            )

        for job in current_jobs:
            if not isinstance(job, dict):
                raise TypeError(
                    f"Malformed job item in run {run_id} (page {page}): "
                    f"expected dict, got {type(job).__name__}."
                )
            job_name = str(job.get("name", "")).strip()

            # Check for conflicting identity fields — NEVER overwrite conflicting fields!
            existing_run_id = job.get("run_id")
            if existing_run_id is not None and str(existing_run_id) != str(run_id):
                raise RuntimeError(
                    f"Job '{job_name}' has conflicting run_id '{existing_run_id}' "
                    f"(expected '{run_id}')."
                )

            existing_sha = job.get("head_sha")
            if existing_sha is not None and str(existing_sha).strip() != clean_sha:
                raise RuntimeError(
                    f"Job '{job_name}' has conflicting head_sha '{existing_sha}' "
                    f"(expected '{clean_sha}')."
                )

            existing_wf = job.get("workflow_path") or job.get("path")
            if existing_wf is not None:
                norm_wf = str(existing_wf).strip().replace("\\", "/").lower()
                if norm_wf != ".github/workflows/ci.yml":
                    raise RuntimeError(
                        f"Job '{job_name}' has conflicting workflow path '{existing_wf}' "
                        "(expected '.github/workflows/ci.yml')."
                    )

            existing_app = job.get("app")
            if isinstance(existing_app, dict):
                slug = str(existing_app.get("slug", "")).strip().lower()
                if slug and slug != "github-actions":
                    raise RuntimeError(
                        f"Job '{job_name}' has conflicting app slug '{slug}' "
                        "(expected 'github-actions')."
                    )

            # Enrich non-conflicting job with required provenance
            job["workflow_run_id"] = run_id
            job["workflow_path"] = ".github/workflows/ci.yml"
            job["head_sha"] = clean_sha
            if not existing_app or not isinstance(existing_app, dict):
                job["app"] = {"slug": "github-actions", "name": "GitHub Actions"}
            if "repository" not in job:
                job["repository"] = repo

            jobs.append(job)

        total_count = j_data.get("total_count")
        if total_count is not None and isinstance(total_count, int) and len(jobs) >= total_count:
            break
        if len(current_jobs) < per_page:
            break
        page += 1

    if not jobs:
        raise RuntimeError(f"Workflow run {run_id} contains no jobs.")

    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify CI check-runs for deployment commit")
    parser.add_argument("--commit-sha", required=True, help="40-character commit SHA to verify")
    parser.add_argument("--repository", default=None, help="GitHub repository (owner/repo)")
    parser.add_argument(
        "--checks-json",
        default=None,
        help="Path to JSON file with check-runs response",
    )
    args = parser.parse_args()

    commit_sha = args.commit_sha.strip()

    try:
        if args.checks_json:
            with open(args.checks_json, encoding="utf-8") as f:
                data = json.load(f)
                check_runs = data.get("check_runs", data) if isinstance(data, dict) else data
        else:
            if not args.repository:
                raise ValueError("Either --checks-json or --repository must be provided.")
            check_runs = fetch_check_runs_via_gh(args.repository, commit_sha)

        verify_deployment_ci(check_runs, commit_sha, expected_repo=args.repository)
        print(f"Verified: All required CI check-runs passed for commit {commit_sha}.")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: CI verification failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
