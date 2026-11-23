locals {
  vpn_exit_node_name = "prod-vpn-exit-01"
}

resource "scaleway_instance_ip" "vpn_exit" {
  project_id = var.project_id
}

resource "scaleway_instance_ip" "vpn_exit_ipv6" {
  project_id = var.project_id
  type       = "routed_ipv6"
}

resource "scaleway_instance_security_group" "vpn_exit" {
  project_id              = var.project_id
  name                    = "${local.vpn_exit_node_name}-sg"
  stateful                = true
  enable_default_security = false
  inbound_default_policy  = "drop"
  outbound_default_policy = "accept"

  inbound_rule {
    action = "accept"
    port   = "22"
  }

  inbound_rule {
    action   = "accept"
    protocol = "UDP"
    port     = "41641"
  }

  outbound_rule {
    action   = "drop"
    protocol = "TCP"
    port     = "25"
  }

  outbound_rule {
    action   = "drop"
    protocol = "TCP"
    port     = "465"
  }
}

resource "scaleway_instance_server" "vpn_exit" {
  project_id = var.project_id
  name       = local.vpn_exit_node_name
  type       = "STARDUST1-S"
  image      = var.instance_image

  root_volume {
    size_in_gb  = 10
    volume_type = "l_ssd"
  }

  ip_ids            = [scaleway_instance_ip.vpn_exit.id, scaleway_instance_ip.vpn_exit_ipv6.id]
  security_group_id = scaleway_instance_security_group.vpn_exit.id
  cloud_init = templatefile("${path.root}/vpn-exit-cloud-init.yaml", {
    node_name = local.vpn_exit_node_name
  })

  tags = ["devsh", "vpn-exit", local.env_slug]

  lifecycle {
    ignore_changes = [cloud_init]
  }
}
