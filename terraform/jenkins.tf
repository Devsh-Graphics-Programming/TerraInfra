locals {
  jenkins_env_prefix  = local.env_slug == "prod" ? "" : "${local.env_slug}."
  jenkins_base_domain = "devsh.eu"
  jenkins_host        = "${local.jenkins_env_prefix}jenkins.${local.jenkins_base_domain}"
}

resource "scaleway_instance_ip" "jenkins" {
  project_id = var.project_id
}

resource "scaleway_instance_security_group" "jenkins" {
  project_id              = var.project_id
  name                    = "jenkins-${local.env_slug}-sg"
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
    action   = "accept"
    port     = "9100"
    ip_range = "${scaleway_instance_ip.observability.address}/32"
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

resource "scaleway_block_volume" "jenkins_data" {
  project_id = var.project_id
  name       = "jenkins-${local.env_slug}-data"
  size_in_gb = var.jenkins_data_volume_size_gb
  iops       = 5000
  tags       = ["jenkins", "ci-controller", "data", local.env_slug]

  lifecycle {
    prevent_destroy = true
  }
}

resource "scaleway_instance_server" "jenkins" {
  project_id = var.project_id

  name  = "jenkins-${local.env_slug}"
  type  = var.jenkins_instance_type
  image = var.instance_image

  root_volume {
    size_in_gb  = 20
    volume_type = "l_ssd"
  }

  ip_id                 = scaleway_instance_ip.jenkins.id
  security_group_id     = scaleway_instance_security_group.jenkins.id
  additional_volume_ids = [scaleway_block_volume.jenkins_data.id]

  cloud_init = templatefile("${path.root}/jenkins-cloud-init.yaml", {
    acme_email      = var.acme_email
    jenkins_host    = local.jenkins_host
    luks_key_access = var.luks_key_access_key
    luks_key_secret = var.luks_key_secret_key
    luks_key_url    = var.luks_key_url
    path_root       = path.root
    swap_file       = var.swap_file
    swap_size_gb    = var.swap_size_gb
    swap_swappiness = var.swap_swappiness
  })

  tags = [
    "jenkins",
    "ci-controller",
    local.env_slug,
  ]

  lifecycle {
    ignore_changes = [cloud_init]
  }
}
