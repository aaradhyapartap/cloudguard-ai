$ErrorActionPreference = "Stop"

Write-Host "== CloudGuard AI Full Validation =="

$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "[1/3] Backend Phase 3 validation"
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "validate-phase3.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Backend Phase 3 validation failed."
}

Write-Host ""
Write-Host "[2/3] Infrastructure validation"

$infrastructure = Join-Path $repoRoot "infrastructure"

if (Test-Path $infrastructure) {
    Push-Location $infrastructure

    try {
        if (Test-Path "requirements.txt") {
            Write-Host "Infrastructure directory detected."
        }

        if (Test-Path "app.py") {
            Write-Host "Running Python syntax validation for infrastructure."
            python -m compileall . -q
            if ($LASTEXITCODE -ne 0) {
                throw "Infrastructure Python syntax validation failed."
            }
        }

        if (Test-Path "tests") {
            Write-Host "Running infrastructure tests."
            python -m pytest -q
            if ($LASTEXITCODE -ne 0) {
                throw "Infrastructure tests failed."
            }
        }
        else {
            Write-Host "No infrastructure tests directory yet; skipping pytest."
        }
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "Infrastructure directory not found; skipping."
}

Write-Host ""
Write-Host "[3/3] Repository checks"

git -C $repoRoot diff --check
if ($LASTEXITCODE -ne 0) {
    throw "git diff --check failed."
}

git -C $repoRoot status --short

Write-Host ""
Write-Host "Full validation passed."
