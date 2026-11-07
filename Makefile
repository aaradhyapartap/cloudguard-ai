.PHONY: help setup up down dev api web migrate test lint typecheck check clean

help:  ## Show available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

setup:  ## Install backend and frontend dependencies
	cd backend && pip install -e ".[dev]"
	cd frontend && npm install
	@test -f .env || cp .env.example .env
	@test -f frontend/.env.local || cp frontend/.env.local.example frontend/.env.local
	@echo "Setup complete. Run 'make up' then 'make migrate'."

up:  ## Start Postgres and LocalStack
	docker compose up -d
	@echo "Waiting for Postgres..."
	@until docker compose exec -T postgres pg_isready -U cloudguard >/dev/null 2>&1; do sleep 1; done
	@echo "Ready."

down:  ## Stop containers
	docker compose down

migrate:  ## Apply database migrations
	cd backend && alembic upgrade head

api:  ## Run the backend with reload
	cd backend && uvicorn app.main:app --reload --port 8000

web:  ## Run the frontend
	cd frontend && npm run dev

test:  ## Run tests that need no database
	cd backend && pytest -m "not integration"

test-db:  ## Run tests that need PostgreSQL
	cd backend && RUN_DB_TESTS=1 pytest -m integration

lint:  ## Lint both sides
	cd backend && ruff check .
	cd frontend && npm run lint

typecheck:  ## Type-check both sides
	cd backend && mypy app
	cd frontend && npm run typecheck

check: lint typecheck test  ## Everything CI runs

synth:  ## Render CloudFormation templates without touching AWS
	cd infrastructure && cdk synth -c offline_synth=true

deploy-identity:  ## Create the Cognito user pool (needs AWS credentials)
	cd infrastructure && cdk deploy CloudGuardIdentity-dev

token:  ## Print a bearer token for the seeded analyst
	@curl -s localhost:8000/api/v1/auth/dev-login \
		-H 'content-type: application/json' \
		-d '{"email":"analyst@acme.test"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'

clean:  ## Remove containers, volumes and caches
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache frontend/.next
