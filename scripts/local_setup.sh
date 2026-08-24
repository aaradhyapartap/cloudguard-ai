#!/usr/bin/env bash
# One-shot local bootstrap: dependencies, containers, migrations, seed data.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Environment files"
[[ -f .env ]] || cp .env.example .env
[[ -f frontend/.env.local ]] || cp frontend/.env.local.example frontend/.env.local

echo "==> Backend dependencies"
cd backend && pip install -e ".[dev]" --quiet && cd ..

echo "==> Frontend dependencies"
cd frontend && npm install --silent && cd ..

echo "==> Containers"
docker compose up -d
echo "==> Waiting for PostgreSQL"
until docker compose exec -T postgres pg_isready -U cloudguard >/dev/null 2>&1; do sleep 1; done

echo "==> Migrations"
cd backend && alembic upgrade head && cd ..

echo "==> Seed data"
python scripts/seed_data.py

cat <<'MSG'

Ready. Two terminals:

  make api      # http://localhost:8000/docs
  make web      # http://localhost:3000

MSG
