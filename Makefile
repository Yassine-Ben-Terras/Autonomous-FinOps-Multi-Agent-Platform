# ═══════════════════════════════════════════════════════════════════════════════
# CloudSense — Developer Makefile
# ═══════════════════════════════════════════════════════════════════════════════

.PHONY: help install dev docker-up docker-down test lint format migrate seed clean build deploy

# ── Default ────────────────────────────────────────────────────────────────────
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Local Development ──────────────────────────────────────────────────────────
install: ## Install production dependencies
	pip install -r requirements.txt

dev: ## Install development dependencies
	pip install -r requirements-dev.txt
	pre-commit install

docker-up: ## Start infrastructure services (ClickHouse, PostgreSQL, Kafka, Redis)
	docker compose -f infra/docker/docker-compose.yml up -d
	@echo "Waiting for services..."
	@sleep 15
	@docker compose -f infra/docker/docker-compose.yml ps

docker-down: ## Stop infrastructure services
	docker compose -f infra/docker/docker-compose.yml down

docker-logs: ## Show infrastructure logs
	docker compose -f infra/docker/docker-compose.yml logs -f

# ── Database ───────────────────────────────────────────────────────────────────
migrate: ## Run ClickHouse migrations
	@echo "Applying ClickHouse migrations..."
	@docker exec -i cloudsense-clickhouse clickhouse-client -d cloudsense < infra/clickhouse/001_focus_billing.sql
	@echo "Migrations applied."

seed: ## Seed test data
	@echo "Seeding test data..."
	python scripts/seed_test_data.py

# ── Testing ────────────────────────────────────────────────────────────────────
test: ## Run all tests
	pytest tests/ -v --tb=short

test-cov: ## Run tests with coverage
	pytest tests/ -v --cov=. --cov-report=term-missing --cov-report=html

test-unit: ## Run unit tests only
	pytest tests/unit/ -v

test-integration: ## Run integration tests
	pytest tests/integration/ -v

# ── Code Quality ───────────────────────────────────────────────────────────────
lint: ## Run linter (ruff)
	ruff check sdk/ connectors/ agents/ services/ recommendations/ policy/ bot/ observability/ tests/

lint-fix: ## Run linter with auto-fix
	ruff check --fix sdk/ connectors/ agents/ services/ recommendations/ policy/ bot/ observability/ tests/

format: ## Format code (ruff format)
	ruff format sdk/ connectors/ agents/ services/ recommendations/ policy/ bot/ observability/ tests/

typecheck: ## Run type checker (mypy)
	mypy sdk/ connectors/ agents/ services/ --ignore-missing-imports

precommit: ## Run all pre-commit hooks
	pre-commit run --all-files

# ── API ────────────────────────────────────────────────────────────────────────
run: ## Start the API server
	uvicorn services.api.main:app --host 0.0.0.0 --port 8000 --reload

run-prod: ## Start the API server (production)
	uvicorn services.api.main:app --host 0.0.0.0 --port 8000 --workers 4

# ── Agents ─────────────────────────────────────────────────────────────────────
agent-analyze: ## Trigger a cross-cloud analysis via API
	curl -X POST http://localhost:8000/api/v1/agents/analyze \
		-H "Content-Type: application/json" \
		-d '{"goal": "Find all cost optimization opportunities", "providers": ["aws", "azure", "gcp"]}'

agent-status: ## Get analysis status (set SESSION_ID)
	@test -n "$(SESSION_ID)" || (echo "Usage: make agent-status SESSION_ID=xxx"; exit 1)
	curl http://localhost:8000/api/v1/agents/status/$(SESSION_ID)

# ── Build & Deploy ─────────────────────────────────────────────────────────────
build: ## Build Docker image
	docker build -t cloudsense/api:0.2.0 .

docker-push: ## Push Docker image
	docker push cloudsense/api:0.2.0

helm-install: ## Install Helm chart
	helm upgrade --install cloudsense infra/helm/cloudsense/ \
		--namespace cloudsense \
		--create-namespace \
		--values infra/helm/cloudsense/values.yaml

helm-uninstall: ## Uninstall Helm chart
	helm uninstall cloudsense --namespace cloudsense

# ── Utilities ──────────────────────────────────────────────────────────────────
clean: ## Clean up generated files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name ".coverage" -delete
	rm -rf htmlcov/ .pytest_cache/ .mypy_cache/

api-docs: ## Open API documentation
	@echo "API docs: http://localhost:8000/docs"

requirements-export: ## Export locked requirements
	pip freeze > requirements.lock.txt
