# CloudSense — Architecture

## Five-Layer Design

CloudSense uses a strict five-layer architecture. Each layer has a single
responsibility. Adding a new cloud provider only requires a new Layer 2 connector.

```
L5  UI & Integrations
    React 18 dashboard (TypeScript + shadcn/ui)
    FastAPI REST & GraphQL API
    Slack bot for recommendations & approvals
    Terraform provider + CDK constructs

L4  Agent Orchestration Engine
    Supervisor agent — LangGraph DAG, ReAct loop
    7 specialized sub-agents with tool-use and memory
    Open Policy Agent (OPA) approval gate
    Action queue with rollback registry

L3  Normalization Engine
    FOCUS 1.0 schema ETL pipelines
    Apache Spark transformation jobs
    dbt models for cost dimensions
    Apache Iceberg table format

L2  Cloud Connectors (read-only by default)
    AWS: Cost Explorer, Trusted Advisor, Compute Optimizer
    Azure: Cost Management API, Advisor, Resource Graph
    GCP: Billing API, Recommender API, BigQuery export
    Kubernetes: Kubecost-compatible metrics

L1  Data & Infrastructure
    ClickHouse — OLAP billing analytics
    PostgreSQL — metadata, config, approvals
    Apache Kafka — real-time billing event stream
    Redis — caching, queues, agent short-term memory
```

## Data Flow

```
Cloud Provider Billing APIs
        │
        ▼
   L2 Connector (read-only)
        │
        ▼
   L3 ETL Pipeline
   (raw → FOCUS 1.0 schema)
        │
        ├──► ClickHouse (hot analytical queries)
        └──► Kafka (real-time stream for Anomaly Agent)
                │
                ▼
          L4 Supervisor Agent
          (LangGraph DAG)
                │
        ┌───────┼───────────────────────────────┐
        │       │       │       │       │       │
        ▼       ▼       ▼       ▼       ▼       ▼
      AWS    Azure   GCP   Anomaly Tagging Forecasting
      Cost   Cost    Cost  Agent   Agent   Agent
      Agent  Agent   Agent
                                        │
                                        ▼
                              Action Agent
                              (after OPA approval)
                                        │
                                        ▼
                              Cloud Provider APIs
                              (write, scoped, gated)
```

## Security Model

- **Read-first**: All connectors are read-only by default.
- **OPA gating**: Every autonomous action must pass an OPA/Rego policy check.
- **Human approval**: Production environments always require explicit sign-off.
- **Rollback registry**: Every action registers a rollback plan; auto-rollback on failure.
- **Immutable audit log**: Every reasoning step, tool call, and action is recorded.
- **Credential isolation**: No secrets in code. HashiCorp Vault or cloud secret managers.

## FOCUS 1.0 Schema

The normalization engine transforms AWS CUR, Azure Cost Export, and GCP Billing
export into a single queryable schema. See `cloudsense/core/models/focus.py` for
the full Pydantic model.

Key dimensions: `billing_account_id`, `service_name`, `region_id`,
`effective_cost`, `list_cost`, `usage_quantity`, `charge_category`, `tags`.
