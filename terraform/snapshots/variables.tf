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
  description = "Legacy single Block Volume name to snapshot. Use snapshot_targets for new configuration."
  default     = "devsh-k3s-prod-data-node1"
}

variable "snapshot_targets" {
  type = map(object({
    volume_name = string
    name_prefix = string
    tags        = optional(list(string), [])
  }))
  description = "Block Volume snapshot targets keyed by stable logical name."
  default = {
    node1-main = {
      volume_name = "devsh-k3s-prod-data-node1"
      name_prefix = "devsh-k3s-prod-data"
      tags        = ["node1", "kimai"]
    }
    chat = {
      volume_name = "prod-chat-01-data"
      name_prefix = "prod-chat-01-data"
      tags        = ["chat", "stoatchat"]
    }
    jenkins = {
      volume_name = "jenkins-prod-data"
      name_prefix = "jenkins-prod-data"
      tags        = ["jenkins", "ci-controller"]
    }
    observability = {
      volume_name = "prod-observability-01-data"
      name_prefix = "prod-observability-01-data"
      tags        = ["observability", "monitoring"]
    }
    vpn-control = {
      volume_name = "prod-vpn-control-01-data"
      name_prefix = "prod-vpn-control-01-data"
      tags        = ["vpn-control", "headscale", "authentik"]
    }
  }
}

variable "auto_snapshot_trigger" {
  type        = string
  description = "Trigger value for the rotating (auto) snapshot. Change it to force a new auto snapshot."
}

variable "manual_snapshots" {
  type = map(object({
    created_at = string
    ttl_hours  = optional(number, 24)
    targets    = optional(list(string))
  }))
  description = "Manual snapshots (keyed by name) that are kept until ttl_hours expires (requires terraform apply to enforce)."
  default     = {}
}

variable "requested_manual_snapshot_name" {
  type        = string
  description = "Optional manual snapshot key requested by the current run (used only for outputs/notifications)."
  default     = ""
}
