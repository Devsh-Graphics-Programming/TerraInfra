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
  default     = "main"
}

variable "config_repo_path" {
  type        = string
  description = "Path inside the config repo with Kubernetes manifests"
  default     = "terraform/k8s"
}

variable "config_pat_token" {
  type        = string
  description = "Fine-grained PAT with read-only access to the manifests repo"
  sensitive   = true
}

variable "flux_hook_domain" {
  type        = string
  description = "Domain name for Flux webhook receiver (e.g., flux-hook.prod.example.com)"
}

variable "github_webhook_secret" {
  type        = string
  description = "Shared secret for GitHub webhook -> Flux receiver"
  sensitive   = true
}

variable "env_name" {
  type        = string
  description = "Environment name"
  default     = "prod"
}
