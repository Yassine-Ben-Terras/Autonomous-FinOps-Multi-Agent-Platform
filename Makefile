.PHONY: help install dev-install test test-cov lint format build docker-up docker-down helm-install helm-uninstall
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
install:
	pip install -r requirements.txt
dev-install:
	pip install -r requirements-dev.txt && pre-commit install
test:
	pytest tests/ -v
test-cov:
	pytest tests/ --cov=cloudsense --cov-report=html --cov-report=term
lint:
	ruff check cloudsense tests && mypy cloudsense
format:
	ruff format cloudsense tests && ruff check --fix cloudsense tests
build:
	docker build -t cloudsense:latest .
docker-up:
	docker compose -f infra/docker/docker-compose.yml up -d
docker-down:
	docker compose -f infra/docker/docker-compose.yml down -v
helm-install:
	helm upgrade --install cloudsense infra/helm/cloudsense/
helm-uninstall:
	helm uninstall cloudsense
migrate:
	alembic upgrade head
seed:
	python -m cloudsense.infra.clickhouse.seed
