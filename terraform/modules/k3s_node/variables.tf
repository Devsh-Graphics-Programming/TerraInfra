variable "project_id" {
  type        = string
  description = "Scaleway Project ID"
}

variable "env_name" {
  type        = string
  description = "Environment name (prod/dev/etc)"
}

variable "data_volume_snapshot_id" {
  type        = string
  description = "Optional snapshot ID used to boot the data volume instead of a blank disk."
  default     = ""
}

variable "prevent_destroy_data_volume" {
  type        = bool
  description = "Set false only when you intentionally want Terraform to allow destroying the data volume (e.g., recreating from scratch/snapshot)."
  default     = true
}

variable "create_daily_snapshot" {
  type        = bool
  description = "When true, creates a rotating daily snapshot of the data volume (usually enabled only for prod)."
  default     = false
}

variable "public_ip_id" {
  type        = string
  description = "Optional existing Flexible IP ID to attach (skips IP creation)."
  default     = ""
}

variable "public_ip_address" {
  type        = string
  description = "Optional existing Flexible IP address to attach (skips IP creation)."
  default     = ""
}

variable "snapshot_rotation_hours" {
  type        = number
  description = "Rotation window (hours) used by the snapshot trigger."
  default     = 24
}

variable "acme_email" {
  type        = string
  description = "Email for ACME"
}

variable "config_repo_url" {
  type        = string
  description = "Git repo URL containing Kubernetes manifests for this environment"
}

variable "config_repo_branch" {
  type        = string
  description = "Branch to track in the config repo"
  default     = "master"
}

variable "config_repo_path" {
  type        = string
  description = "Path inside the config repo with Kubernetes manifests"
  default     = "terraform/k8s"
}

variable "github_persistent_terra_infra_ro_pat" {
  type        = string
  description = "Fine-grained PAT (read-only) kept in cluster for repo access"
  sensitive   = true
}

variable "github_bootstrap_terra_infra_webhook_pat" {
  type        = string
  description = "Fine-grained PAT used only during bootstrap to create/patch GitHub webhook (not persisted)"
  sensitive   = true
  default     = ""
}

variable "cloud_init" {
  type        = string
  description = "Rendered cloud-init user data"
}

variable "with_vs_code_server" {
  type        = bool
  description = "Expose and install VS Code Server for remote debugging (port 8080)"
  default     = false
}
