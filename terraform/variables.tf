variable "project_id" {
  type        = string
  description = "Scaleway Project ID"
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

variable "env_name" {
  type        = string
  description = "Environment name"
  default     = "prod"
}

variable "luks_key_access_key" {
  type        = string
  description = "Access key (read-only) for fetching LUKS key from Object Storage"
  sensitive   = true
  default     = ""
}

variable "luks_key_secret_key" {
  type        = string
  description = "Secret key (read-only) for fetching LUKS key from Object Storage"
  sensitive   = true
  default     = ""
}

variable "luks_key_url" {
  type        = string
  description = "Optional presigned URL to fetch LUKS key (overrides access/secret when set)"
  default     = ""
}

variable "allow_fresh_bootstrap" {
  type        = bool
  description = "Set to true when you intentionally want to recreate secrets (new volume or clean data)."
  default     = false
}

variable "sops_age_key" {
  type        = string
  description = "Age private key used by Flux to decrypt SOPS-managed secrets (optional)."
  sensitive   = true
  default     = ""
}

variable "data_volume_snapshot_id" {
  type        = string
  description = "Optional snapshot ID used by environments that should attach a copy of the prod data volume."
  default     = ""
}

variable "prevent_destroy_data_volume" {
  type        = bool
  description = "Set false only when you intentionally want Terraform to allow destroying the data volume (e.g., wiping/recreating)."
  default     = true
}

variable "public_ip_id" {
  type        = string
  description = "Existing Flexible IP ID to attach (leave empty to let Terraform create one)."
  default     = ""
}

variable "public_ip_address" {
  type        = string
  description = "Existing Flexible IP address to attach (alternative to public_ip_id)."
  default     = ""
}

variable "manual_snapshots" {
  type = map(object({
    created_at = string
    ttl_hours  = optional(number, 24)
  }))
  description = "Manual data-volume snapshots (keyed by name) that are kept until ttl_hours expires (requires terraform apply to enforce)."
  default     = {}
}
