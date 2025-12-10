terraform {
  required_providers {
    scaleway = {
      source = "scaleway/scaleway"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.10"
    }
  }
}

locals {
  snapshot_enabled = var.create_daily_snapshot && var.env_name == "prod"
}

resource "scaleway_instance_ip" "public_ip" {
  project_id = var.project_id
}

resource "scaleway_instance_security_group" "web_sg" {
  project_id = var.project_id
  name       = "devsh-k3s-sg-${var.env_name}"
  stateful   = true

  inbound_default_policy  = "drop"
  outbound_default_policy = "accept"

  inbound_rule {
    action = "accept"
    port   = "22"
  }

  inbound_rule {
    action = "accept"
    port   = "80"
  }

  inbound_rule {
    action = "accept"
    port   = "443"
  }
}

resource "scaleway_instance_server" "k3s_node_1" {
  project_id = var.project_id

  name  = "devsh-k3s-${var.env_name}-node-1"
  type  = "DEV1-M"
  image = "ubuntu_jammy"
  root_volume {
    size_in_gb  = 40
    volume_type = "l_ssd"
  }

  ip_id             = scaleway_instance_ip.public_ip.id
  security_group_id = scaleway_instance_security_group.web_sg.id

  cloud_init = var.cloud_init

  tags = [
    "devsh",
    "k3s",
    "kimai-node",
    var.env_name,
  ]

  additional_volume_ids = [scaleway_block_volume.data_volume.id]

  lifecycle {
    ignore_changes = [cloud_init]
  }
}

resource "scaleway_block_volume" "data_volume" {
  project_id  = var.project_id
  name        = "devsh-k3s-${var.env_name}-data-node1"
  size_in_gb  = 10
  iops        = 5000
  tags        = ["devsh", "k3s", "data", var.env_name]
  snapshot_id = var.data_volume_snapshot_id == "" ? null : var.data_volume_snapshot_id

  lifecycle {}
}

resource "time_rotating" "snapshot_trigger" {
  count          = local.snapshot_enabled ? 1 : 0
  rotation_hours = var.snapshot_rotation_hours
}

resource "scaleway_block_snapshot" "data_volume" {
  count = local.snapshot_enabled ? 1 : 0
  name = format(
    "devsh-k3s-%s-data-snapshot-%s",
    var.env_name,
    replace(
      replace(
        replace(time_rotating.snapshot_trigger[0].rotation_rfc3339, ":", "-"),
        "T",
        "-"
      ),
      "Z",
      ""
    )
  )
  project_id = var.project_id
  volume_id  = scaleway_block_volume.data_volume.id
  tags       = ["devsh", "k3s", "snapshot", var.env_name]

  lifecycle {
    create_before_destroy = true # keep previous snapshot until the new one exists
  }
}
