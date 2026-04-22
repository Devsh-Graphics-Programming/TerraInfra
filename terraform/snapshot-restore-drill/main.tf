locals {
  safe_target_key = replace(var.target_key, "/", "-")
  resource_prefix = "restore-drill-${local.safe_target_key}-${var.run_id}"
}

resource "scaleway_instance_ip" "restore_drill" {
  project_id = var.project_id
}

resource "scaleway_instance_security_group" "restore_drill" {
  project_id              = var.project_id
  name                    = "${local.resource_prefix}-sg"
  stateful                = true
  enable_default_security = false

  inbound_default_policy  = "drop"
  outbound_default_policy = "accept"

  inbound_rule {
    action   = "accept"
    port     = "22"
    ip_range = var.ssh_cidr
  }
}

resource "scaleway_block_volume" "restore_drill" {
  project_id  = var.project_id
  name        = "${local.resource_prefix}-data"
  iops        = 5000
  snapshot_id = var.snapshot_id
  tags        = ["devsh", "restore-drill", var.target_key, var.run_id]
}

resource "scaleway_instance_server" "restore_drill" {
  project_id = var.project_id

  name  = local.resource_prefix
  type  = var.instance_type
  image = var.instance_image

  root_volume {
    size_in_gb  = 20
    volume_type = "l_ssd"
  }

  ip_id                 = scaleway_instance_ip.restore_drill.id
  security_group_id     = scaleway_instance_security_group.restore_drill.id
  additional_volume_ids = [scaleway_block_volume.restore_drill.id]

  user_data = {
    cloud-init = templatefile("${path.root}/cloud-init.yaml", {
      ensure_data_mount_b64 = base64encode(file("${path.root}/../bootstrap/ensure-data-mount.sh"))
      restore_drill_b64     = base64encode(file("${path.root}/scripts/restore-drill.sh"))
      luks_key_access_key   = var.luks_key_access_key
      luks_key_secret_key   = var.luks_key_secret_key
      luks_key_url          = var.luks_key_url
      ssh_public_key        = var.ssh_public_key
      target_key            = var.target_key
      run_id                = var.run_id
    })
  }

  tags = [
    "devsh",
    "restore-drill",
    var.target_key,
    var.run_id,
  ]
}
