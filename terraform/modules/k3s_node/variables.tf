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

variable "cloud_init" {
  type        = string
  description = "Rendered cloud-init user data"
}
