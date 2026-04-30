-- ============================================================
-- CloudSense — PostgreSQL Schema
-- Stores: connector config, users, approvals, audit log
-- ============================================================

-- ── Extensions ───────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Connectors ────────────────────────────────────────────────
-- Stores configured cloud connector credentials (encrypted at rest)
CREATE TABLE IF NOT EXISTS connectors (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider            VARCHAR(10)  NOT NULL CHECK (provider IN ('aws', 'azure', 'gcp')),
    name                VARCHAR(255) NOT NULL,
    billing_account_id  VARCHAR(255) NOT NULL,
    billing_account_name VARCHAR(255),
    -- Credentials stored as pgp-encrypted JSON (key from Vault)
    credentials_enc     BYTEA,
    -- Status tracking
    status              VARCHAR(20)  NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'paused', 'error', 'pending')),
    last_error          TEXT,
    -- Ingestion schedule
    schedule_cron       VARCHAR(50)  DEFAULT '0 6 * * *',  -- daily at 06:00 UTC
    -- Audit
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          UUID,
    -- Ingestion tracking
    last_ingested_at    TIMESTAMPTZ,
    last_ingested_records BIGINT,
    UNIQUE (provider, billing_account_id)
);

CREATE INDEX idx_connectors_provider ON connectors (provider);
CREATE INDEX idx_connectors_status   ON connectors (status);

-- ── Users ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    display_name    VARCHAR(255),
    password_hash   VARCHAR(255),               -- null for SSO-only users
    -- Roles: viewer | analyst | admin
    role            VARCHAR(20) NOT NULL DEFAULT 'viewer'
                    CHECK (role IN ('viewer', 'analyst', 'admin')),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    -- SSO
    sso_provider    VARCHAR(50),                -- 'google', 'github', 'saml'
    sso_subject     VARCHAR(255),
    -- Tokens
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_email ON users (email);

-- ── API Keys ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS api_keys (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash    VARCHAR(255) NOT NULL UNIQUE,  -- sha256 of the actual key
    name        VARCHAR(255) NOT NULL,
    scopes      TEXT[] NOT NULL DEFAULT ARRAY['read'],
    expires_at  TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Ingestion Jobs ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    connector_id    UUID NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
    -- Celery task ID for status lookups
    task_id         VARCHAR(255) UNIQUE,
    status          VARCHAR(20) NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'success', 'failed', 'cancelled')),
    -- Period covered by this ingestion
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    -- Results
    records_fetched  BIGINT DEFAULT 0,
    records_inserted BIGINT DEFAULT 0,
    error_message    TEXT,
    -- Timing
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    duration_s      FLOAT GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (finished_at - started_at))
    ) STORED
);

CREATE INDEX idx_jobs_connector ON ingestion_jobs (connector_id);
CREATE INDEX idx_jobs_status    ON ingestion_jobs (status);
CREATE INDEX idx_jobs_queued    ON ingestion_jobs (queued_at DESC);

-- ── Action Approvals ──────────────────────────────────────────
-- Every autonomous action from Phase 4 is tracked here
CREATE TABLE IF NOT EXISTS action_approvals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- The agent that raised this action
    agent_name      VARCHAR(100) NOT NULL,
    provider        VARCHAR(10)  NOT NULL,
    connector_id    UUID REFERENCES connectors(id),
    -- What the action does
    action_type     VARCHAR(100) NOT NULL,  -- e.g. 'right_size_ec2', 'stop_idle_vm'
    resource_id     VARCHAR(500) NOT NULL,
    resource_name   VARCHAR(500),
    -- Financial impact
    estimated_monthly_savings NUMERIC(12, 4),
    current_monthly_cost      NUMERIC(12, 4),
    -- Agent reasoning
    reasoning       TEXT,
    recommendation  JSONB,                  -- full structured recommendation from agent
    -- Approval workflow
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'executed', 'rolled_back', 'expired')),
    approved_by     UUID REFERENCES users(id),
    approved_at     TIMESTAMPTZ,
    rejected_reason TEXT,
    -- Execution tracking
    executed_at     TIMESTAMPTZ,
    execution_log   TEXT,
    rollback_plan   JSONB,                  -- stored for 7-day rollback window
    rolled_back_at  TIMESTAMPTZ,
    -- Expiry (pending approvals expire after 7 days)
    expires_at      TIMESTAMPTZ NOT NULL DEFAULT now() + INTERVAL '7 days',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_approvals_status    ON action_approvals (status);
CREATE INDEX idx_approvals_connector ON action_approvals (connector_id);
CREATE INDEX idx_approvals_expires   ON action_approvals (expires_at)
    WHERE status = 'pending';

-- ── Audit Log (immutable) ─────────────────────────────────────
-- Append-only log of every system event; never update, never delete
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    -- Actor
    actor_id    UUID,                   -- null for system actions
    actor_type  VARCHAR(20) NOT NULL    -- 'user' | 'agent' | 'system'
                CHECK (actor_type IN ('user', 'agent', 'system')),
    actor_name  VARCHAR(255),
    -- Action
    event_type  VARCHAR(100) NOT NULL,  -- 'ingestion.started', 'action.approved', etc.
    resource_type VARCHAR(100),
    resource_id VARCHAR(500),
    -- Payload
    payload     JSONB,
    -- Outcome
    status      VARCHAR(20) DEFAULT 'success',
    error       TEXT,
    -- Context
    ip_address  INET,
    user_agent  VARCHAR(500),
    -- Timing
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (occurred_at);

-- Monthly partitions for the audit log
CREATE TABLE IF NOT EXISTS audit_log_2024_01 PARTITION OF audit_log
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
CREATE TABLE IF NOT EXISTS audit_log_2024_02 PARTITION OF audit_log
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
-- Add more as needed — a cron job auto-creates next month's partition

CREATE INDEX idx_audit_actor    ON audit_log (actor_id, occurred_at DESC);
CREATE INDEX idx_audit_event    ON audit_log (event_type, occurred_at DESC);
CREATE INDEX idx_audit_resource ON audit_log (resource_type, resource_id);

-- ── Budget Alerts ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS budget_alerts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    connector_id    UUID REFERENCES connectors(id),
    -- Scope of the budget
    scope_type      VARCHAR(50) NOT NULL   -- 'account' | 'service' | 'team' | 'tag'
                    CHECK (scope_type IN ('account', 'service', 'team', 'tag')),
    scope_value     VARCHAR(255),          -- e.g. team='platform'
    -- Budget definition
    budget_amount   NUMERIC(14, 4) NOT NULL,
    period_type     VARCHAR(20)  NOT NULL DEFAULT 'monthly'
                    CHECK (period_type IN ('daily', 'weekly', 'monthly')),
    -- Thresholds that trigger notifications (0.0–1.0)
    thresholds      FLOAT[] NOT NULL DEFAULT ARRAY[0.50, 0.80, 1.00],
    -- Notifications
    notify_emails   TEXT[],
    notify_slack    VARCHAR(255),          -- Slack channel
    -- Status
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Trigger: auto-update updated_at ──────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_connectors_updated_at
    BEFORE UPDATE ON connectors
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_budget_alerts_updated_at
    BEFORE UPDATE ON budget_alerts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── Seed: default admin user (change password immediately!) ──
INSERT INTO users (email, display_name, role, password_hash)
VALUES (
    'admin@cloudsense.local',
    'CloudSense Admin',
    'admin',
    crypt('ChangeMe123!', gen_salt('bf'))
) ON CONFLICT (email) DO NOTHING;

-- ── Phase 4: Action Approvals ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS action_approvals (
    id                  TEXT PRIMARY KEY,
    recommendation_id   TEXT NOT NULL,
    provider            TEXT NOT NULL,
    environment         TEXT NOT NULL CHECK (environment IN ('development','staging','production')),
    action_type         TEXT NOT NULL,
    target_resource_id  TEXT NOT NULL,
    parameters          JSONB NOT NULL DEFAULT '{}',
    rollback_plan       JSONB NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','awaiting_approval','approved','rejected',
                                              'executing','completed','failed','rolled_back')),
    requested_by        TEXT NOT NULL,
    approved_by         TEXT,
    rejected_reason     TEXT,
    executed_at         TIMESTAMPTZ,
    expires_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_action_approvals_status
    ON action_approvals (status, environment, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_action_approvals_resource
    ON action_approvals (target_resource_id);

-- ── Phase 4: Audit Log (append-only, no UPDATE/DELETE) ────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    event_type    TEXT        NOT NULL,
    actor_id      TEXT,
    resource_type TEXT        NOT NULL,
    resource_id   TEXT        NOT NULL,
    payload       JSONB       NOT NULL DEFAULT '{}',
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partition by month for scalability
CREATE INDEX IF NOT EXISTS idx_audit_log_resource
    ON audit_log (resource_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_event_type
    ON audit_log (event_type, occurred_at DESC);

-- Prevent UPDATE / DELETE on audit_log (append-only rule)
CREATE OR REPLACE RULE audit_log_no_update AS
    ON UPDATE TO audit_log DO INSTEAD NOTHING;

CREATE OR REPLACE RULE audit_log_no_delete AS
    ON DELETE TO audit_log DO INSTEAD NOTHING;

-- ── Phase 5.1: Multi-tenant Tables ───────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS tenants (
    id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name                TEXT NOT NULL,
    slug                TEXT NOT NULL UNIQUE,
    plan                TEXT NOT NULL DEFAULT 'community'
                            CHECK (plan IN ('community','enterprise')),
    sso_enabled         BOOLEAN NOT NULL DEFAULT false,
    sso_provider        TEXT CHECK (sso_provider IN ('saml','oidc')),
    saml_metadata_url   TEXT,
    oidc_issuer         TEXT,
    oidc_client_id      TEXT,
    oidc_client_secret  TEXT,
    max_users           INTEGER NOT NULL DEFAULT 10,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    email           TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL DEFAULT '',
    password_hash   TEXT NOT NULL DEFAULT 'sso-provisioned',
    mfa_enabled     BOOLEAN NOT NULL DEFAULT false,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_members (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'viewer'
                    CHECK (role IN ('admin','engineer','viewer','billing')),
    is_active   BOOLEAN NOT NULL DEFAULT true,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_members_tenant ON tenant_members (tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_tenant_members_user   ON tenant_members (user_id);

-- Default dev tenant
INSERT INTO tenants (id, name, slug, plan)
VALUES ('dev-tenant', 'CloudSense Dev', 'dev', 'enterprise')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO users (id, email, display_name, password_hash)
VALUES (
    'dev-admin-user',
    'admin@cloudsense.local',
    'CloudSense Admin',
    crypt('ChangeMe123!', gen_salt('bf'))
) ON CONFLICT (email) DO NOTHING;

INSERT INTO tenant_members (tenant_id, user_id, role)
VALUES ('dev-tenant', 'dev-admin-user', 'admin')
ON CONFLICT (tenant_id, user_id) DO NOTHING;
