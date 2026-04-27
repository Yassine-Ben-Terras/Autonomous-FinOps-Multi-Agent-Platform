# ============================================================
# CloudSense — AWS IAM Policy (Terraform)
# Grants the minimal read-only permissions required by the
# AWS Cost Connector and Trusted Advisor integration.
# ============================================================
# Usage:
#   terraform init
#   terraform apply -var="cloudsense_account_id=<MANAGEMENT_ACCOUNT>"
# ============================================================

terraform {
  required_version = ">= 1.8"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "cloudsense_account_id" {
  description = "AWS account ID where CloudSense is deployed"
  type        = string
}

variable "external_id" {
  description = "External ID for cross-account role assumption (security best practice)"
  type        = string
  default     = "cloudsense-connector"
}

# ── IAM Policy — read-only billing + advisor ──────────────────
resource "aws_iam_policy" "cloudsense_readonly" {
  name        = "CloudSenseReadOnly"
  description = "Read-only access for CloudSense FinOps connector"
  path        = "/cloudsense/"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Cost Explorer — billing data & anomalies
      {
        Sid    = "CostExplorerReadOnly"
        Effect = "Allow"
        Action = [
          "ce:GetCostAndUsage",
          "ce:GetCostAndUsageWithResources",
          "ce:GetCostForecast",
          "ce:GetDimensionValues",
          "ce:GetReservationCoverage",
          "ce:GetReservationPurchaseRecommendation",
          "ce:GetReservationUtilization",
          "ce:GetSavingsPlansCoverage",
          "ce:GetSavingsPlansUtilization",
          "ce:GetSavingsPlansPurchaseRecommendation",
          "ce:GetAnomalies",
          "ce:GetAnomalyMonitors",
          "ce:GetAnomalySubscriptions",
          "ce:ListTagsForResource",
        ]
        Resource = "*"
      },
      # Trusted Advisor & Compute Optimizer
      {
        Sid    = "TrustedAdvisorReadOnly"
        Effect = "Allow"
        Action = [
          "trustedadvisor:Describe*",
          "trustedadvisor:List*",
          "compute-optimizer:GetEC2InstanceRecommendations",
          "compute-optimizer:GetAutoScalingGroupRecommendations",
          "compute-optimizer:GetEBSVolumeRecommendations",
          "compute-optimizer:GetLambdaFunctionRecommendations",
          "compute-optimizer:GetECSServiceRecommendations",
        ]
        Resource = "*"
      },
      # Resource tagging — read tags across all resources
      {
        Sid    = "ResourceTaggingReadOnly"
        Effect = "Allow"
        Action = [
          "tag:GetResources",
          "tag:GetTagKeys",
          "tag:GetTagValues",
          "resourcegroupstaggingapi:GetResources",
        ]
        Resource = "*"
      },
      # Organizations — list accounts in the org (for multi-account)
      {
        Sid    = "OrganizationsReadOnly"
        Effect = "Allow"
        Action = [
          "organizations:ListAccounts",
          "organizations:DescribeOrganization",
          "organizations:ListTagsForResource",
        ]
        Resource = "*"
      },
      # EC2 — describe resources for idle detection (read-only)
      {
        Sid    = "EC2DescribeReadOnly"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeVolumes",
          "ec2:DescribeSnapshots",
          "ec2:DescribeAddresses",
          "ec2:DescribeLoadBalancers",
          "ec2:DescribeNatGateways",
          "ec2:DescribeReservedInstances",
          "ec2:DescribeSpotInstanceRequests",
        ]
        Resource = "*"
      },
      # RDS describe
      {
        Sid    = "RDSDescribeReadOnly"
        Effect = "Allow"
        Action = [
          "rds:DescribeDBInstances",
          "rds:DescribeDBClusters",
          "rds:DescribeReservedDBInstances",
          "rds:ListTagsForResource",
        ]
        Resource = "*"
      },
      # CloudWatch metrics — for utilisation data used by anomaly agent
      {
        Sid    = "CloudWatchReadOnly"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListMetrics",
        ]
        Resource = "*"
      },
    ]
  })

  tags = {
    "managed-by" = "cloudsense"
    "purpose"    = "finops-connector"
  }
}

# ── Cross-account IAM Role ────────────────────────────────────
# Allows CloudSense (running in cloudsense_account_id) to assume this role
resource "aws_iam_role" "cloudsense_connector" {
  name        = "CloudSenseConnector"
  description = "Cross-account role for CloudSense FinOps connector"
  path        = "/cloudsense/"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowCloudSenseAssumeRole"
      Effect = "Allow"
      Principal = {
        AWS = "arn:aws:iam::${var.cloudsense_account_id}:root"
      }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "sts:ExternalId" = var.external_id
        }
      }
    }]
  })

  tags = {
    "managed-by" = "cloudsense"
    "purpose"    = "finops-connector"
  }
}

resource "aws_iam_role_policy_attachment" "cloudsense_attach" {
  role       = aws_iam_role.cloudsense_connector.name
  policy_arn = aws_iam_policy.cloudsense_readonly.arn
}

# ── Outputs ───────────────────────────────────────────────────
output "role_arn" {
  description = "ARN to configure in CloudSense connector settings"
  value       = aws_iam_role.cloudsense_connector.arn
}

output "external_id" {
  description = "External ID to configure in CloudSense connector settings"
  value       = var.external_id
}
