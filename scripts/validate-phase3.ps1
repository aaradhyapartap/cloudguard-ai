$ErrorActionPreference = "Stop"

Write-Host "== CloudGuard AI Phase 3 Validation =="

$repoRoot = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repoRoot "backend"

Push-Location $backend

try {
    Write-Host ""
    Write-Host "[1/4] Ruff"
    python -m ruff check .
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff validation failed."
    }

    Write-Host ""
    Write-Host "[2/4] mypy"
    python -m mypy app
    if ($LASTEXITCODE -ne 0) {
        throw "mypy validation failed."
    }

    Write-Host ""
    Write-Host "[3/4] pytest with tenant RLS role"
    $env:DB_USER = "cloudguard_app"
    $env:DB_PASSWORD = "cloudguard_app"
    $env:RUN_DB_TESTS = "1"

    python -m pytest -ra
    if ($LASTEXITCODE -ne 0) {
        throw "pytest validation failed."
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "[4/4] Git whitespace check"
git -C $repoRoot diff --check
if ($LASTEXITCODE -ne 0) {
    throw "git diff --check failed."
}

Write-Host ""
Write-Host "Phase 3 validation passed."
