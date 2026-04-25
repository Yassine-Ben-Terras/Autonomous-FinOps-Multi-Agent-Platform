<div align="center">

# ☁️ CloudSense

**Autonomous FinOps Multi-Agent Platform**

Autonomously detect, analyze & reduce cloud costs across AWS, Azure & GCP

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-green)
![FinOps FOCUS](https://img.shields.io/badge/FinOps-FOCUS%201.0-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Tests](https://img.shields.io/badge/Tests-passing-brightgreen)

</div>

---

## Overview

CloudSense is a production-grade, open-source FinOps platform that orchestrates
**8 specialized AI agents** to continuously monitor, analyze, and optimize cloud
spend across AWS, Azure, and GCP — all normalized under the
[FinOps FOCUS 1.0](https://focus.finops.org/) specification.

> **Status:** 🚧 Active development — Phase 1 (Foundation & Connectors)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  L5  UI & Integrations      React Dashboard · REST API · Slack  │
├─────────────────────────────────────────────────────────────────┤
│  L4  Agent Orchestration    Supervisor (LangGraph) · 7 Agents   │
├─────────────────────────────────────────────────────────────────┤
│  L3  Normalization Engine   FOCUS 1.0 ETL · Spark · dbt         │
├─────────────────────────────────────────────────────────────────┤
│  L2  Cloud Connectors       AWS · Azure · GCP · Kubernetes       │
├─────────────────────────────────────────────────────────────────┤
│  L1  Data & Infrastructure  ClickHouse · Postgres · Kafka · Redis│
└─────────────────────────────────────────────────────────────────┘
```

## Agents

| Agent | Role |
|-------|------|
| **Supervisor** | Orchestrates all sub-agents via LangGraph DAG (ReAct reasoning) |
| **AWS Cost** | Cost Explorer, Trusted Advisor, Compute Optimizer |
| **Azure Cost** | Cost Management API, Advisor recommendations |
| **GCP Cost** | BigQuery billing exports, Recommender API |
| **Anomaly** | Real-time spike detection (Prophet + ARIMA on Kafka stream) |
| **Tagging** | Compliance scan + LLM-based tag inference |
| **Forecasting** | 30/60/90-day projections (Prophet + XGBoost) |
| **Action** | Approved execution with Terraform/SDK + rollback registry |
| **Reporting** | FOCUS-compliant reports, Grafana, Slack, BI exports |

---

## Quickstart (Local Development)

### Prerequisites
- Python 3.12+
- Docker & Docker Compose
- Make

### 1. Clone & configure

```bash
git clone https://github.com/Yassine-Ben-Terras/Autonomous-FinOps-Multi-Agent-Platform.git
cd cloudsense
cp .env.example .env
# Edit .env with your cloud credentials and LLM API key
```

### 2. Start infrastructure

```bash
make docker-up
# ClickHouse, PostgreSQL, Kafka, Redis spin up
```

### 3. Install dependencies & run tests

```bash
make install-dev
make test
```

### 4. Start the API

```bash
make dev
# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

---

## Development Roadmap

| Phase | Months | What ships |
|-------|--------|-----------|
| **1 — Foundation** | 1–2 | FOCUS ETL, Cloud connectors, Dashboard, REST API |
| **2 — Agent Engine** | 3–4 | Supervisor + specialist agents, OPA policy gate, Slack bot |
| **3 — ML** | 5–6 | Anomaly detection, Forecasting, Budget alerts |
| **4 — Autonomous Actions** | 7–8 | Action agent, Rollback registry, Tagging agent |
| **5 — Enterprise** | 9–12 | Multi-tenant SaaS, Grafana plugin, Plugin SDK |

---

## Project Structure

```
cloudsense/
├── cloudsense/             # Application source
│   ├── core/               # Shared models, config, utilities
│   │   └── models/         # FOCUS 1.0 schema + domain models
│   ├── connectors/         # AWS / Azure / GCP connectors (Phase 1)
│   ├── etl/                # FOCUS normalization pipeline (Phase 1)
│   ├── agents/             # Supervisor + specialist agents (Phase 2)
│   └── api/                # FastAPI application (Phase 1)
├── tests/                  # Mirrored test structure
├── docker/                 # Per-service Dockerfiles + init scripts
├── infra/                  # Helm charts (Phase 5)
├── docs/                   # Architecture, runbooks
├── .env.example            # Environment variable template
├── docker-compose.yml      # Local dev stack
└── Makefile                # All common tasks
```

---

## Standards & Compliance

- **FinOps FOCUS 1.0** — All billing data normalized to the open standard
- **FinOps Foundation** — Practitioner-aligned, FOCUS working group registered
- **OpenCost compatible** — Kubernetes cost allocation API

---

## License

MIT — see [LICENSE](LICENSE)

---

## Contributing

Contributions welcome! See [docs/contributing.md](docs/contributing.md) for guidelines.
