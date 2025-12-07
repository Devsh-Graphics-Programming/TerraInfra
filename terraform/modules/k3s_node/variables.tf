variable "project_id" {
  type        = string
  description = "Scaleway Project ID"
}

variable "env_name" {
  type        = string
  description = "Environment name (prod/dev/etc)"
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
  description = "Domain name for Flux webhook receiver"
}

variable "cloud_init" {
  type        = string
  description = "Rendered cloud-init user data"
}
