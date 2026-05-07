# ============================================================
# CloudSense Phase 4 — Action Execution IAM Roles (Terraform)
#
# Write-scoped IAM roles assumed per-action via STS.
# Follows least-privilege: each action type has its own role.
# Separate from the read-only connector roles in aws_iam.tf.
#
# Destructive actions (EBS delete, EIP release) are disabled by
# default. Set enable_destructive_actions=true only after testing.
#
# Usage:
#   terraform init
#   terraform apply -var="cloudsense_account_id=<account_id>"
# ============================================================

terraform {
  required_version = ">= 1.8"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "cloudsense_account_id" {
  type        = string
  description = "AWS account ID where CloudSense is deployed"
}

variable "external_id" {
  type        = string
  default     = "cloudsense-action-executor"
  description = "External ID for STS assume-role — prevents confused-deputy attack"
}

variable "enable_destructive_actions" {
  type        = bool
  default     = false
  description = "Enable delete/release roles. Set true only after validating in non-prod."
}

locals {
  trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowCloudSenseExecutor"
      Effect = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.cloudsense_account_id}:root" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "sts:ExternalId" = var.external_id } }
    }]
  })
  common_tags = { "managed-by" = "cloudsense", "phase" = "4" }
}

# ── 1. EC2 Stop / Start ────────────────────────────────────────
resource "aws_iam_policy" "ec2_stop_start" {
  name        = "CloudSenseActionEC2StopStart"
  path        = "/cloudsense/actions/"
  description = "Stop and start EC2 instances tagged cloudsense-managed=true"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "EC2StopStart"
      Effect = "Allow"
      Action = [
        "ec2:StartInstances", "ec2:StopInstances",
        "ec2:DescribeInstances", "ec2:DescribeInstanceStatus",
      ]
      Resource  = "*"
      Condition = { StringEquals = { "ec2:ResourceTag/cloudsense-managed" = "true" } }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role" "ec2_stop_start" {
  name               = "CloudSenseActionEC2StopStart"
  path               = "/cloudsense/actions/"
  assume_role_policy = local.trust_policy
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ec2_stop_start" {
  role       = aws_iam_role.ec2_stop_start.name
  policy_arn = aws_iam_policy.ec2_stop_start.arn
}

# ── 2. EC2 Right-size ──────────────────────────────────────────
resource "aws_iam_policy" "ec2_rightsize" {
  name        = "CloudSenseActionEC2Rightsize"
  path        = "/cloudsense/actions/"
  description = "Modify EC2 instance type for right-sizing"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "EC2Rightsize"
      Effect = "Allow"
      Action = [
        "ec2:StopInstances", "ec2:StartInstances",
        "ec2:ModifyInstanceAttribute",
        "ec2:DescribeInstances", "ec2:DescribeInstanceStatus",
        "ec2:DescribeInstanceTypes",
      ]
      Resource = "*"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role" "ec2_rightsize" {
  name               = "CloudSenseActionEC2Rightsize"
  path               = "/cloudsense/actions/"
  assume_role_policy = local.trust_policy
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ec2_rightsize" {
  role       = aws_iam_role.ec2_rightsize.name
  policy_arn = aws_iam_policy.ec2_rightsize.arn
}

# ── 3. EBS Cleanup — disabled by default ──────────────────────
resource "aws_iam_policy" "ebs_cleanup" {
  count       = var.enable_destructive_actions ? 1 : 0
  name        = "CloudSenseActionEBSCleanup"
  path        = "/cloudsense/actions/"
  description = "Snapshot then delete unattached EBS volumes tagged cloudsense-managed=true"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EBSSnapshot"
        Effect = "Allow"
        Action = ["ec2:CreateSnapshot", "ec2:DescribeSnapshots", "ec2:DescribeVolumes"]
        Resource = "*"
      },
      {
        Sid      = "EBSDelete"
        Effect   = "Allow"
        Action   = ["ec2:DeleteVolume"]
        Resource = "*"
        Condition = { StringEquals = { "ec2:ResourceTag/cloudsense-managed" = "true" } }
      },
    ]
  })
  tags = local.common_tags
}

resource "aws_iam_role" "ebs_cleanup" {
  count              = var.enable_destructive_actions ? 1 : 0
  name               = "CloudSenseActionEBSCleanup"
  path               = "/cloudsense/actions/"
  assume_role_policy = local.trust_policy
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ebs_cleanup" {
  count      = var.enable_destructive_actions ? 1 : 0
  role       = aws_iam_role.ebs_cleanup[0].name
  policy_arn = aws_iam_policy.ebs_cleanup[0].arn
}

# ── 4. EIP Release — disabled by default ──────────────────────
resource "aws_iam_policy" "eip_release" {
  count       = var.enable_destructive_actions ? 1 : 0
  name        = "CloudSenseActionEIPRelease"
  path        = "/cloudsense/actions/"
  description = "Release unassociated Elastic IPs"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "EIPRelease"
      Effect   = "Allow"
      Action   = ["ec2:ReleaseAddress", "ec2:DescribeAddresses"]
      Resource = "*"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role" "eip_release" {
  count              = var.enable_destructive_actions ? 1 : 0
  name               = "CloudSenseActionEIPRelease"
  path               = "/cloudsense/actions/"
  assume_role_policy = local.trust_policy
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "eip_release" {
  count      = var.enable_destructive_actions ? 1 : 0
  role       = aws_iam_role.eip_release[0].name
  policy_arn = aws_iam_policy.eip_release[0].arn
}

# ── 5. Resource Tagging ────────────────────────────────────────
resource "aws_iam_policy" "tagging" {
  name        = "CloudSenseActionTagging"
  path        = "/cloudsense/actions/"
  description = "Apply and remove tags across all resource types (tagging agent)"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "TaggingWrite"
      Effect = "Allow"
      Action = [
        "tag:TagResources", "tag:UntagResources",
        "resourcegroupstaggingapi:TagResources",
        "resourcegroupstaggingapi:UntagResources",
        "resourcegroupstaggingapi:GetResources",
      ]
      Resource = "*"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role" "tagging" {
  name               = "CloudSenseActionTagging"
  path               = "/cloudsense/actions/"
  assume_role_policy = local.trust_policy
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "tagging" {
  role       = aws_iam_role.tagging.name
  policy_arn = aws_iam_policy.tagging.arn
}

# ── Outputs ────────────────────────────────────────────────────
output "ec2_stop_start_role_arn" {
  value       = aws_iam_role.ec2_stop_start.arn
  description = "Set as AWS_ACTION_ROLE_EC2_STOP_START in .env"
}

output "ec2_rightsize_role_arn" {
  value       = aws_iam_role.ec2_rightsize.arn
  description = "Set as AWS_ACTION_ROLE_EC2_RIGHTSIZE in .env"
}

output "tagging_role_arn" {
  value       = aws_iam_role.tagging.arn
  description = "Set as AWS_ACTION_ROLE_TAGGING in .env"
}

output "ebs_cleanup_role_arn" {
  value       = var.enable_destructive_actions ? aws_iam_role.ebs_cleanup[0].arn : "disabled"
  description = "Set enable_destructive_actions=true to activate"
}

output "external_id" {
  value       = var.external_id
  description = "Set as ACTION_EXTERNAL_ID in .env"
}
