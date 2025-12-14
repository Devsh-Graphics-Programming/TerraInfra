variable "project_id" {
  type        = string
  description = "Scaleway Project ID"
}

variable "env_name" {
  type        = string
  description = "Environment name used in snapshot naming"
  default     = "prod"
}

variable "volume_name" {
  type        = string
  description = "Block Volume name to snapshot"
  default     = "devsh-k3s-prod-data-node1"
}

