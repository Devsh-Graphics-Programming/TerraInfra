terraform {
  required_providers {
    scaleway = {
      source = "scaleway/scaleway"
    }
  }
}

locals {
  manual_snapshots_enabled = var.env_name == "prod"
  manual_snapshots_active = {
    for name, cfg in var.manual_snapshots :
    name => {
      created_at = cfg.created_at
      ttl_hours  = try(cfg.ttl_hours, 24)
    }
    if local.manual_snapshots_enabled && timecmp(plantimestamp(), timeadd(cfg.created_at, format("%dh", try(cfg.ttl_hours, 24)))) == -1
  }
}

data "scaleway_instance_ip" "by_id" {
  count = var.public_ip_id != "" ? 1 : 0
  id    = var.public_ip_id
}

data "scaleway_instance_ip" "by_address" {
  count   = var.public_ip_id == "" && var.public_ip_address != "" ? 1 : 0
  address = var.public_ip_address
}

resource "scaleway_instance_ip" "public_ip" {
  count      = var.public_ip_id == "" && var.public_ip_address == "" ? 1 : 0
  project_id = var.project_id
}

locals {
  resolved_ip_id = var.public_ip_id != "" ? data.scaleway_instance_ip.by_id[0].id : (
    var.public_ip_address != "" ? data.scaleway_instance_ip.by_address[0].id : scaleway_instance_ip.public_ip[0].id
  )
  resolved_ip_address = var.public_ip_id != "" ? data.scaleway_instance_ip.by_id[0].address : (
    var.public_ip_address != "" ? data.scaleway_instance_ip.by_address[0].address : scaleway_instance_ip.public_ip[0].address
  )
}

resource "scaleway_instance_security_group" "web_sg" {
  project_id              = var.project_id
  name                    = "devsh-k3s-sg-${var.env_name}"
  stateful                = true
  enable_default_security = false

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

  outbound_rule {
    action   = "drop"
    protocol = "TCP"
    port     = "25"
    ip_range = "0.0.0.0/0"
  }

  outbound_rule {
    action   = "drop"
    protocol = "TCP"
    port     = "465"
    ip_range = "0.0.0.0/0"
  }

  outbound_rule {
    action   = "drop"
    protocol = "TCP"
    port     = "25"
    ip_range = "::/0"
  }

  outbound_rule {
    action   = "drop"
    protocol = "TCP"
    port     = "465"
    ip_range = "::/0"
  }
}

resource "scaleway_instance_server" "k3s_node_1" {
  project_id = var.project_id

  name  = "devsh-k3s-${var.env_name}-node-1"
  type  = "DEV1-M"
  image = var.instance_image
  root_volume {
    size_in_gb  = 40
    volume_type = "l_ssd"
  }

  ip_id             = local.resolved_ip_id
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

resource "scaleway_block_snapshot" "manual_data_volume" {
  for_each   = local.manual_snapshots_active
  project_id = var.project_id
  volume_id  = scaleway_block_volume.data_volume.id
  name       = format("devsh-k3s-%s-data-manual-%s", var.env_name, trimprefix(each.key, "manual-"))
  tags = [
    "devsh",
    "k3s",
    "snapshot",
    var.env_name,
    "manual",
    "ttl_hours=${each.value.ttl_hours}",
  ]

  lifecycle {
    create_before_destroy = true
  }
}
