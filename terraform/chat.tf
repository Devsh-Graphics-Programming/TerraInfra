locals {
  chat_node_name = "prod-chat-01"
  stoat_host     = "stoatchat.devsh.eu"
}

resource "scaleway_instance_ip" "chat" {
  project_id = var.project_id
}

resource "scaleway_instance_security_group" "chat" {
  project_id              = var.project_id
  name                    = "prod-chat-01-sg"
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

  inbound_rule {
    action = "accept"
    port   = "7881"
  }

  inbound_rule {
    action     = "accept"
    protocol   = "UDP"
    port_range = "50000-50100"
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

resource "scaleway_block_volume" "chat_data" {
  project_id = var.project_id
  name       = "prod-chat-01-data"
  size_in_gb = var.chat_data_volume_size_gb
  iops       = 5000
  tags       = ["devsh", "stoatchat", "data", local.env_slug]

  lifecycle {
    prevent_destroy = true
  }
}

resource "scaleway_instance_server" "chat" {
  project_id = var.project_id

  name  = local.chat_node_name
  type  = var.chat_instance_type
  image = var.instance_image

  root_volume {
    size_in_gb  = 40
    volume_type = "l_ssd"
  }

  ip_id                 = scaleway_instance_ip.chat.id
  security_group_id     = scaleway_instance_security_group.chat.id
  additional_volume_ids = [scaleway_block_volume.chat_data.id]

  cloud_init = templatefile("${path.root}/chat-cloud-init.yaml", {
    acme_email      = var.acme_email
    luks_key_access = var.luks_key_access_key
    luks_key_secret = var.luks_key_secret_key
    luks_key_url    = var.luks_key_url
    path_root       = path.root
    stoat_host      = local.stoat_host
    swap_file       = var.swap_file
    swap_size_gb    = var.swap_size_gb
    swap_swappiness = var.swap_swappiness
  })

  tags = [
    "devsh",
    "stoatchat",
    local.env_slug,
  ]

  lifecycle {
    ignore_changes = [cloud_init]
  }
}
