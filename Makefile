# ============================================================
# CloudSense — Makefile
# ============================================================
# make up          Start full dev stack (Docker Compose)
# make down        Stop all services
# make test        Run unit tests
# make test-int    Run integration tests (requires running services)
# make lint        Run ruff + mypy
# make migrate     Run Alembic migrations
# make ch-init     Apply ClickHouse DDL
# make topics      Create Kafka topics (KRaft, no ZooKeeper)
# make scan        Trigger AWS ingestion (requires AWS_ACCOUNT_ID env)
# make logs        Tail API logs
# ============================================================

.DEFAULT_GOAL := help
COMPOSE        = docker compose -f infra/docker/docker-compose.yml
KAFKA_BROKER   ?= localhost:9092
API_URL        ?= http://localhost:8000

.PHONY: help up down restart logs lint test test-int \
        ch-init topics migrate scan clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Docker Compose ────────────────────────────────────────────
up: ## Start all services in the background
	$(COMPOSE) up -d
	@echo ""
	@echo "  Services starting — wait ~30s for Kafka KRaft to be ready"
	@echo "  API:       $(API_URL)/docs"
	@echo "  Kafka UI:  http://localhost:8080"
	@echo "  Grafana:   http://localhost:3001  (admin/admin)"

down: ## Stop all services
	$(COMPOSE) down

restart: ## Restart the API service only (faster than full down/up)
	$(COMPOSE) restart api

logs: ## Tail API logs
	$(COMPOSE) logs -f api

logs-all: ## Tail all service logs
	$(COMPOSE) logs -f

status: ## Show service health
	$(COMPOSE) ps

# ── Initialisation ────────────────────────────────────────────
ch-init: ## Apply ClickHouse DDL (focus schema + tables + MVs)
	@echo "Applying ClickHouse DDL..."
	docker exec cloudsense-clickhouse \
		clickhouse-client \
		--user cloudsense \
		--password "$${CLICKHOUSE_PASSWORD:-dev_password_change_me}" \
		--multiquery < infra/clickhouse/001_focus_billing.sql
	@echo "Done."

topics: ## Create Kafka topics (KRaft broker must be running)
	@echo "Creating Kafka topics (KRaft — no ZooKeeper)..."
	python -c "
from cloudsense.infra.kafka.producer import FocusBillingProducer, KafkaConfig
p = FocusBillingProducer(KafkaConfig(bootstrap_servers='$(KAFKA_BROKER)'))
p.ensure_topics_exist()
print('Topics created successfully')
"

migrate: ## Run Alembic database migrations
	alembic -c services/api/db/alembic.ini upgrade head

migrate-generate: ## Generate a new Alembic migration
	@read -p "Migration name: " name; \
	alembic -c services/api/db/alembic.ini revision --autogenerate -m "$$name"

# ── Development ───────────────────────────────────────────────
install: ## Install Python dependencies (dev mode)
	pip install poetry==1.8.3
	poetry install

lint: ## Run ruff lint + format check + mypy
	ruff check .
	ruff format --check .
	mypy cloudsense/ services/ --ignore-missing-imports

lint-fix: ## Auto-fix lint issues
	ruff check . --fix
	ruff format .

# ── Tests ──────────────────────────────────────────────────────
test: ## Run unit tests with coverage
	pytest tests/unit/ -v \
		--cov=cloudsense \
		--cov=services \
		--cov-report=term-missing \
		--cov-fail-under=70

test-int: ## Run integration tests (requires running services)
	ENV=test \
	KAFKA_BOOTSTRAP_SERVERS=$(KAFKA_BROKER) \
	CLICKHOUSE_HOST=localhost \
	pytest tests/integration/ -v --timeout=60

test-all: test test-int ## Run all tests

# ── CLI shortcuts ─────────────────────────────────────────────
scan: ## Trigger AWS ingestion (set AWS_ACCOUNT_ID in env)
	python -m services.cli.main scan \
		--provider aws \
		--connector-id "$${AWS_ACCOUNT_ID}" \
		--api-url $(API_URL)

overview: ## Show cost overview in terminal
	python -m services.cli.main costs overview \
		--days 30 \
		--api-url $(API_URL)

top-services: ## Show top 10 services by cost
	python -m services.cli.main costs by-service \
		--top 10 \
		--api-url $(API_URL)

# ── Kafka helpers ─────────────────────────────────────────────
kafka-list-topics: ## List all Kafka topics
	docker exec cloudsense-kafka \
		kafka-topics --bootstrap-server localhost:9092 --list

kafka-describe-topic: ## Describe the billing topic
	docker exec cloudsense-kafka \
		kafka-topics --bootstrap-server localhost:9092 \
		--describe --topic focus.billing.raw

kafka-consumer-groups: ## List consumer groups (KRaft — no ZooKeeper command needed)
	docker exec cloudsense-kafka \
		kafka-consumer-groups --bootstrap-server localhost:9092 --list

# ── Cleanup ────────────────────────────────────────────────────
clean: ## Remove all Docker volumes (DELETES ALL DATA)
	@read -p "This deletes all data. Are you sure? [y/N] " confirm; \
	[ "$$confirm" = "y" ] && $(COMPOSE) down -v || echo "Aborted."

clean-cache: ## Remove Python caches
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	find . -type f -name "*.pyc" -delete 2>/dev/null; true
	rm -rf .pytest_cache .mypy_cache .ruff_cache target/
