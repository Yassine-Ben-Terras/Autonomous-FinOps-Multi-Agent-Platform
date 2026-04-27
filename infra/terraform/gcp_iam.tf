# ============================================================
# CloudSense — GCP IAM (Terraform)
# Creates a service account with the minimum roles required
# to read billing data and recommendations.
# ============================================================

terraform {
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
}

variable "gcp_project_id" {
  description = "GCP project where CloudSense runs"
  type        = string
}

variable "billing_account_id" {
  description = "GCP billing account ID to grant access to"
  type        = string
}

variable "bq_dataset_project" {
  description = "GCP project that hosts the BigQuery billing export"
  type        = string
}

variable "bq_dataset_id" {
  description = "BigQuery dataset name for billing export"
  type        = string
  default     = "cloudsense_billing_export"
}

# ── Service Account ───────────────────────────────────────────
resource "google_service_account" "cloudsense" {
  project      = var.gcp_project_id
  account_id   = "cloudsense-connector"
  display_name = "CloudSense FinOps Connector"
  description  = "Read-only service account for CloudSense billing data access"
}

# ── BigQuery read access on the billing export dataset ────────
resource "google_bigquery_dataset_iam_member" "bq_viewer" {
  project    = var.bq_dataset_project
  dataset_id = var.bq_dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.cloudsense.email}"
}

# BigQuery job runner — needed to execute queries
resource "google_project_iam_member" "bq_job_user" {
  project = var.bq_dataset_project
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.cloudsense.email}"
}

# ── Recommender API — for right-sizing suggestions ────────────
resource "google_project_iam_member" "recommender_viewer" {
  project = var.gcp_project_id
  role    = "roles/recommender.viewer"
  member  = "serviceAccount:${google_service_account.cloudsense.email}"
}

# ── Compute read — for idle instance detection ────────────────
resource "google_project_iam_member" "compute_viewer" {
  project = var.gcp_project_id
  role    = "roles/compute.viewer"
  member  = "serviceAccount:${google_service_account.cloudsense.email}"
}

# ── Service account key (for non-GKE deployments) ────────────
# If running on GKE, use Workload Identity instead (more secure)
resource "google_service_account_key" "cloudsense_key" {
  service_account_id = google_service_account.cloudsense.name
  public_key_type    = "TYPE_X509_PEM_FILE"
}

output "service_account_email" {
  value = google_service_account.cloudsense.email
}

output "service_account_key_base64" {
  description = "Base64-encoded service account key JSON. Store in Vault."
  value       = google_service_account_key.cloudsense_key.private_key
  sensitive   = true
}
