.PHONY: help install install-dev test test-cov lint format type-check clean \
        docker-up docker-down migrate

PYTHON  := python3
PYTEST  := python -m pytest
RUFF    := python -m ruff
MYPY    := python -m mypy

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install:  ## Install production dependencies
	pip install -r requirements.txt

install-dev:  ## Install development dependencies
	pip install -r requirements-dev.txt
	pre-commit install

test:  ## Run all tests
	$(PYTEST) tests/ -v

test-cov:  ## Run tests with coverage report
	$(PYTEST) tests/ --cov=cloudsense --cov-report=term-missing --cov-report=html

test-fast:  ## Run tests without coverage (fast)
	$(PYTEST) tests/ -x -q

lint:  ## Lint code with ruff
	$(RUFF) check cloudsense/ tests/

format:  ## Auto-format code with ruff
	$(RUFF) format cloudsense/ tests/

type-check:  ## Run mypy type checking
	$(MYPY) cloudsense/

check: lint type-check test  ## Run all quality gates

docker-up:  ## Start all services via Docker Compose
	docker compose up -d

docker-down:  ## Stop all services
	docker compose down

docker-logs:  ## Follow logs from all services
	docker compose logs -f

docker-build:  ## Build Docker images
	docker compose build

migrate:  ## Run database migrations
	alembic upgrade head

migrate-new:  ## Create a new Alembic migration (provide MSG=...)
	alembic revision --autogenerate -m "$(MSG)"

clean:  ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache htmlcov .coverage coverage.xml

dev:  ## Run the API in development mode with auto-reload
	uvicorn cloudsense.api.main:app --reload --host 0.0.0.0 --port 8000
