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
- **Orchestrates** AI agents (Phase 2+) for anomaly detection, right-sizing, and autonomous optimisation

---

## 🚀 Quickstart (5 minutes)

### Prerequisites
- Docker & Docker Compose
- At least one cloud account with billing API access

```bash
# 1. Clone the repo
git clone https://github.com/your-org/cloudsense && cd cloudsense

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

# 7. Open the Grafana dashboard
open http://localhost:3001   # admin / admin
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 5 — UI & Integrations                                    │
│  React Dashboard · REST API · Slack Bot · Terraform Provider    │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4 — Agent Orchestration (Phase 2)                        │
│  Supervisor Agent → LangGraph DAG → 7 Specialist Agents        │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3 — FOCUS Normalisation Engine                           │
│  ETL Pipelines · dbt Models · Apache Spark                     │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2 — Cloud Connectors (read-only)                         │
│  AWS Cost Explorer · Azure Cost Mgmt · GCP BigQuery Export      │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1 — Data Infrastructure                                  │
│  ClickHouse · PostgreSQL · Kafka KRaft · Redis · MinIO          │
└─────────────────────────────────────────────────────────────────┘
```

### Why Kafka KRaft (no ZooKeeper)?

CloudSense uses **Kafka 3.7+ in KRaft mode** — the ZooKeeper-free architecture introduced by [KIP-500](https://cwiki.apache.org/confluence/display/KAFKA/KIP-500%3A+Replace+ZooKeeper+with+a+Self-Managed+Metadata+Quorum).

Benefits:
- ✅ **Simpler ops** — one fewer system to manage, monitor, and scale
- ✅ **Faster recovery** — no ZooKeeper lag on broker failover
- ✅ **More partitions** — KRaft scales to millions of partitions vs ZooKeeper's ~200k limit
- ✅ **Production-ready** — Confluent marked KRaft GA in Kafka 3.3

---

## 📁 Project Structure

```
cloudsense/
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
│   │       └── connectors.py            # /api/v1/connectors/*
│   └── ingestion/
│       └── tasks.py                     # Celery billing ingestion tasks
├── infra/
│   ├── clickhouse/
│   │   └── 001_focus_billing.sql        # DDL + materialized views
│   ├── kafka/
│   │   ├── producer.py                  # Kafka KRaft producer
│   │   └── kraft.properties             # Broker config (no ZooKeeper)
│   └── docker/
│       └── docker-compose.yml           # Full dev stack
├── dbt/
│   └── models/
│       ├── staging/stg_focus_billing.sql
│       └── mart/mart_daily_cost_by_service.sql
└── tests/
    └── unit/test_connectors.py
```

---

## 🔒 Security

- All cloud connectors are **read-only** by default
- Credentials are never stored in code — use environment variables or Vault
- Every connector uses **least-privilege IAM** (see `docs/iam/` for policy templates)
- Production actions (Phase 4) require **explicit approval** before execution

---

## 📊 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness probe |
| GET | `/api/v1/costs/overview` | Multi-cloud spend summary |
| GET | `/api/v1/costs/by-service` | Daily cost by service |
| GET | `/api/v1/costs/by-team` | Tag-based team allocation |
| GET | `/api/v1/costs/top-services` | Top N services by cost |
| POST | `/api/v1/ingestion/trigger` | Trigger billing pull |
| GET | `/api/v1/connectors` | List configured connectors |

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🗺️ Roadmap

- **Phase 1** ✅ Foundation: FOCUS schema, connectors, ClickHouse, API
- **Phase 2** 🔄 Agent Engine: LangGraph supervisor + cost analysis agents
- **Phase 3** ⏳ Forecasting: Prophet-based 30/60/90-day projections
- **Phase 4** ⏳ Autonomous Actions: Terraform-level right-sizing with rollback
- **Phase 5** ⏳ Enterprise: SSO, multi-tenant, Grafana plugin, SDK

---

## 🤝 Contributing

PRs are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

## 📄 License

MIT — see [LICENSE](LICENSE).

---

*CloudSense is aligned with the [FinOps Foundation FOCUS 1.0 specification](https://focus.finops.org/).*
