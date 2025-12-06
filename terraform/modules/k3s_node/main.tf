terraform {
  required_providers {
    scaleway = {
      source = "scaleway/scaleway"
    }
  }
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
    size_in_gb = 40
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

  lifecycle {
    ignore_changes = [cloud_init]
  }
}
