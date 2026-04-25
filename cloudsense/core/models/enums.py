"""
CloudSense — Core Enumerations
Aligned with the FinOps FOCUS 1.0 specification.
"""
from enum import Enum


class CloudProvider(str, Enum):
    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    KUBERNETES = "kubernetes"


class ChargeCategory(str, Enum):
    """FOCUS 1.0 ChargeCategory dimension."""
    USAGE = "Usage"
    TAX = "Tax"
    CREDIT = "Credit"
    ADJUSTMENT = "Adjustment"
    PURCHASE = "Purchase"


class ChargeFrequency(str, Enum):
    ONE_TIME = "One-Time"
    RECURRING = "Recurring"
    USAGE_BASED = "Usage-Based"


class CommitmentDiscountType(str, Enum):
    RESERVED_INSTANCE = "ReservedInstance"
    SAVINGS_PLAN = "SavingsPlan"
    COMMITTED_USE_DISCOUNT = "CommittedUseDiscount"
    AZURE_RESERVATION = "AzureReservation"
    HYBRID_BENEFIT = "HybridBenefit"


class ResourceStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    ORPHANED = "orphaned"
    OVER_PROVISIONED = "over_provisioned"
    UNDER_PROVISIONED = "under_provisioned"
    UNKNOWN = "unknown"


class AgentName(str, Enum):
    SUPERVISOR = "supervisor"
    AWS_COST = "aws_cost"
    AZURE_COST = "azure_cost"
    GCP_COST = "gcp_cost"
    ANOMALY = "anomaly"
    TAGGING = "tagging"
    FORECASTING = "forecasting"
    ACTION = "action"
    REPORTING = "reporting"


class ActionStatus(str, Enum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
