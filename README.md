# CloudSense ⚡ — FinOps Multi-Agent Platform

> Autonomously detect, analyze, and reduce cloud costs across **AWS · Azure · GCP**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![FOCUS 1.0](https://img.shields.io/badge/FinOps-FOCUS%201.0-purple.svg)](https://focus.finops.org)
[![Kafka KRaft](https://img.shields.io/badge/Kafka-KRaft%20(no%20ZooKeeper)-red.svg)](https://kafka.apache.org)

---

## ✨ What it does

CloudSense is an open-source, production-grade FinOps platform that:

- **Normalises** billing data from AWS, Azure, and GCP into a single **FOCUS 1.0** schema
- **Streams** all cost events through **Kafka in KRaft mode** (ZooKeeper-free) for real-time processing
- **Stores** billing data in **ClickHouse** for sub-second OLAP queries at hundreds of millions of rows
- **Exposes** a **FastAPI** REST API with cost overview, drill-down, and team allocation endpoints
- **Orchestrates** AI agents (Phase 2) for cost analysis, idle resource detection, right-sizing, and commitment optimization
- **Recommends** concrete actions with projected savings, risk levels, and approval workflows
- **Delivers** recommendations via Slack with interactive approve/reject buttons
- **Enforces** OPA policies for safe autonomous action gating

---

## 🚀 Quickstart

### Prerequisites
- Docker & Docker Compose
- At least one cloud account with billing API access

```bash
# 1. Clone the repo
git clone https://github.com/Yassine-Ben-Terras/Autonomous-FinOps-Multi-Agent-Platform.git && cd cloudsense

# 2. Copy the example environment file
cp .env.example .env
# Edit .env with your cloud credentials

# 3. Start the full stack (Kafka KRaft + ClickHouse + PostgreSQL + Redis + API)
docker compose -f infra/docker/docker-compose.yml up -d

# 4. Wait for services to be healthy (~60 seconds)
docker compose ps

# 5. Trigger your first billing ingestion (AWS example)
curl -X POST http://localhost:8000/api/v1/ingestion/trigger \
  -H "Content-Type: application/json" \
  -d '{"provider": "aws", "connector_id": "YOUR_ACCOUNT_ID"}'

# 6. View the cost overview
curl http://localhost:8000/api/v1/costs/overview

# 7. Trigger an AI agent analysis
curl -X POST http://localhost:8000/api/v1/agents/analyze \
  -H "Content-Type: application/json" \
  -d '{"goal": "Find cost optimization opportunities", "providers": ["aws", "azure", "gcp"]}'

# 8. Open the API docs
open http://localhost:8000/docs
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 5 — UI & Integrations                                    │
│  React Dashboard · REST API · Slack Bot · Terraform Provider    │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4 — Agent Orchestration (Phase 2)                        │
│  Supervisor Agent → LangGraph DAG → 7 Specialist Agents         │
│  Recommendation Engine · OPA Policy Engine · Slack Bot          │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3 — FOCUS Normalisation Engine                           │
│  ETL Pipelines · dbt Models · Apache Spark                      │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2 — Cloud Connectors (read-only)                         │
│  AWS Cost Explorer · Azure Cost Mgmt · GCP BigQuery Export      │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1 — Data Infrastructure                                  │
│  ClickHouse · PostgreSQL · Kafka KRaft · Redis · MinIO          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📁 Project Structure

```
cloudsense/
├── agents/                              # Phase 2 — Agent Engine
│   ├── supervisor/
│   │   └── supervisor.py                # LangGraph DAG orchestrator
│   ├── specialist/
│   │   ├── aws_agent.py                 # AWS cost analysis agent
│   │   ├── azure_agent.py               # Azure cost analysis agent
│   │   └── gcp_agent.py                 # GCP cost analysis agent
│   ├── tools/
│   │   └── cost_tools.py                # Shared agent tools
│   └── shared_types.py                  # Pydantic models for agent state
├── connectors/
│   ├── aws/         cost_connector.py   # AWS Cost Explorer → FOCUS
│   ├── azure/       cost_connector.py   # Azure Cost Mgmt → FOCUS
│   └── gcp/         cost_connector.py   # GCP BigQuery export → FOCUS
├── sdk/
│   └── focus_schema.py                  # FOCUS 1.0 Pydantic models
├── services/
│   ├── api/
│   │   ├── main.py                      # FastAPI app
│   │   ├── config.py                    # pydantic-settings config
│   │   ├── db/clickhouse.py             # ClickHouse client
│   │   └── routers/
│   │       ├── costs.py                 # /api/v1/costs/*
│   │       ├── ingestion.py             # /api/v1/ingestion/*
│   │       ├── connectors.py            # /api/v1/connectors/*
│   │       └── agents.py                # /api/v1/agents/* (Phase 2)
│   └── ingestion/
│       └── tasks.py                     # Celery billing ingestion tasks
├── recommendations/
│   └── engine.py                        # Cost optimization engine
├── policy/
│   ├── engine.py                        # OPA policy evaluator
│   └── rego/cloudsense.rego             # Rego policies
├── bot/
│   └── slack_bot.py                     # Slack integration
├── observability/
│   └── tracing.py                       # OpenTelemetry + LangSmith
├── infra/
│   ├── clickhouse/                      # DDL + materialized views
│   ├── kafka/                           # KRaft producer + config
│   ├── helm/cloudsense/                 # Kubernetes Helm charts
│   └── docker/docker-compose.yml        # Full dev stack
├── dbt/models/                          # dbt transformations
├── tests/                               # pytest test suite
├── Dockerfile                           # Multi-stage production build
├── Makefile                             # Developer commands
└── .env.example                         # Configuration template
```

---

## 🤖 Phase 2 — Agent Engine

The Agent Engine is the core intelligence layer of CloudSense. It uses a **LangGraph supervisor** to orchestrate specialist cost analysis agents across all three clouds.

### How it works

1. **Supervisor Agent** receives a natural language goal (e.g., "Find cost savings")
2. It **decomposes** the goal into tasks and dispatches to specialist agents
3. **Specialist agents** (AWS, Azure, GCP) analyze billing data using ClickHouse queries
4. Each agent produces **CostInsight** objects with quantified savings
5. The supervisor **synthesizes** insights into prioritized **RecommendationResult** objects
6. The **OPA Policy Engine** evaluates each recommendation for safety
7. **Slack Bot** delivers recommendations with interactive approve/reject buttons

### Agent capabilities

| Agent | Detects | Savings Focus |
|-------|---------|---------------|
| AWS Cost Agent | Idle EC2/RDS, RI gaps, old instances, tag issues | Compute, Storage |
| Azure Cost Agent | Idle VMs, AHUB gaps, unused disks, advisor recs | Compute, Database |
| GCP Cost Agent | Idle GCE, CUD gaps, unattached disks, GKE waste | Compute, Data |

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/agents/analyze` | Trigger multi-cloud analysis |
| GET | `/api/v1/agents/status/{id}` | Get analysis status & results |
| GET | `/api/v1/agents/history` | List recent analysis jobs |
| POST | `/api/v1/agents/quick/{provider}` | Quick single-provider analysis |
| GET | `/api/v1/agents/insights` | List all insights with filters |
| GET | `/api/v1/agents/recommendations/{id}/roi` | Calculate ROI for recommendation |
| GET | `/api/v1/agents/supervisor/graph` | View LangGraph DAG structure |

---

## 🔒 Security

- All cloud connectors are **read-only** by default
- Credentials are never stored in code — use environment variables or Vault
- Every connector uses **least-privilege IAM**
- **OPA Policy Engine** gates all autonomous actions
- Production changes require **human approval** via Slack
- Full **audit trail** for every agent reasoning step and action

---

## 📊 All API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/ready` | Readiness probe |
| GET | `/api/v1/costs/overview` | Multi-cloud spend summary |
| GET | `/api/v1/costs/by-service` | Daily cost by service |
| GET | `/api/v1/costs/by-team` | Tag-based team allocation |
| GET | `/api/v1/costs/top-services` | Top N services by cost |
| GET | `/api/v1/costs/trend` | Daily cost trend |
| POST | `/api/v1/ingestion/trigger` | Trigger billing pull |
| GET | `/api/v1/ingestion/status/{id}` | Check ingestion status |
| GET | `/api/v1/connectors` | List configured connectors |
| GET | `/api/v1/connectors/health` | Connector health check |
| POST | `/api/v1/connectors/{provider}/test` | Test connector auth |
| POST | `/api/v1/agents/analyze` | Trigger agent analysis |
| GET | `/api/v1/agents/status/{id}` | Get analysis results |
| GET | `/api/v1/agents/history` | List analysis jobs |
| GET | `/api/v1/agents/insights` | List cost insights |

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🧪 Testing

```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Run specific test modules
pytest tests/unit/test_focus_schema.py -v
pytest tests/unit/test_agents.py -v
pytest tests/unit/test_policy_engine.py -v
```

---

## 🚢 Deployment

### Docker

```bash
make build       # Build production image
make docker-push # Push to registry
```

### Kubernetes (Helm)

```bash
make helm-install   # Install/upgrade
make helm-uninstall # Remove
```

The Helm chart includes:
- Deployment with 2+ replicas
- Horizontal Pod Autoscaler (2-10 pods)
- Pod anti-affinity for high availability
- Service, ingress templates
- Secret management
- Resource limits and security contexts

---

## 🗺️ Roadmap

| Phase | Status | Deliverables |
|-------|--------|--------------|
| Phase 1 — Foundation | ✅ Complete | FOCUS schema, connectors, ClickHouse, REST API, Docker Compose |
| Phase 2 — Agent Engine | ✅ Complete | LangGraph supervisor, 3 specialist agents, recommendation engine, OPA policies, Slack bot, observability, Helm charts |
| Phase 3 — Forecasting | ⏳ Planned | Prophet-based 30/60/90-day projections, anomaly detection, budget alerts |
| Phase 4 — Autonomous Actions | ⏳ Planned | Terraform execution, rollback registry, action agent |
| Phase 5 — Enterprise | ⏳ Planned | SSO, multi-tenant, Grafana plugin, Python SDK |

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

*CloudSense is aligned with the [FinOps Foundation FOCUS 1.0 specification](https://focus.finops.org/).*
