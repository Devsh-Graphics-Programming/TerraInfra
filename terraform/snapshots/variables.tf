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

variable "auto_snapshot_trigger" {
  type        = string
  description = "Trigger value for the rotating (auto) snapshot. Change it to force a new auto snapshot."
}

variable "manual_snapshots" {
  type = map(object({
    created_at = string
    ttl_hours  = optional(number, 24)
  }))
  description = "Manual snapshots (keyed by name) that are kept until ttl_hours expires (requires terraform apply to enforce)."
  default     = {}
}

variable "requested_manual_snapshot_name" {
  type        = string
  description = "Optional manual snapshot key requested by the current run (used only for outputs/notifications)."
  default     = ""
}
