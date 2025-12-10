variable "project_id" {
  type        = string
  description = "Scaleway Project ID"
}

variable "kimai_domain" {
  type        = string
  description = "Domain for Kimai"
}

variable "monitoring_domain" {
  type        = string
  description = "Domain for monitoring (Grafana)"
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

variable "flux_hook_domain" {
  type        = string
  description = "Domain name for Flux webhook receiver (e.g., flux-hook.prod.example.com)"
}

variable "website_domain" {
  type        = string
  description = "Domain for the main Caddy-hosted website (e.g., www.devsh.eu)"
  default     = "www.devsh.eu"
}

variable "blog_domain" {
  type        = string
  description = "Domain for the blog site (e.g., blog.devsh.eu)"
  default     = "blog.devsh.eu"
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

variable "snapshot_rotation_hours" {
  type        = number
  description = "Rotation period in hours for the production block-volume snapshot."
  default     = 24
}

variable "data_volume_snapshot_id" {
  type        = string
  description = "Optional snapshot ID used by environments that should attach a copy of the prod data volume."
  default     = ""
}
