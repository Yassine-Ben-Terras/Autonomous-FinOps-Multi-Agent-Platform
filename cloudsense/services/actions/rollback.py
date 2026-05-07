"""
CloudSense Phase 4 — Rollback Registry (services layer)
=========================================================
Thin re-export of RollbackRegistry from the agent specialist module
so API routers and workers can import from a stable path:

    from cloudsense.services.actions.rollback import RollbackRegistry

Canonical implementation:
    cloudsense/agents/specialist/action_agent.py → class RollbackRegistry

The registry stores rollback plans in-process (dict) and optionally
persists to Postgres via ActionLogRepository. Rollback window: 7 days.
"""
from __future__ import annotations

from cloudsense.agents.specialist.action_agent import RollbackRegistry  # noqa: F401

__all__ = ["RollbackRegistry"]
