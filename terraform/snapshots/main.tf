data "scaleway_block_volume" "data_volume" {
  name       = var.volume_name
  project_id = var.project_id
}

resource "time_static" "snapshot_trigger" {
  triggers = {
    bucket = var.auto_snapshot_trigger
  }
}

locals {
  manual_snapshots_active = {
    for name, cfg in var.manual_snapshots :
    name => {
      created_at = cfg.created_at
      ttl_hours  = try(cfg.ttl_hours, 24)
    }
    if timecmp(plantimestamp(), timeadd(cfg.created_at, format("%dh", try(cfg.ttl_hours, 24)))) == -1
  }
}

resource "scaleway_block_snapshot" "data_volume" {
  project_id = var.project_id
  volume_id  = data.scaleway_block_volume.data_volume.id
  name = format(
    "devsh-k3s-%s-data-snapshot-%s",
    var.env_name,
    replace(
      replace(
        replace(time_static.snapshot_trigger.rfc3339, ":", "-"),
        "T",
        "-"
      ),
      "Z",
      ""
    )
  )
  tags = [
    "devsh",
    "k3s",
    "snapshot",
    var.env_name,
    "managed=auto",
  ]

  lifecycle {
    create_before_destroy = true
    replace_triggered_by  = [time_static.snapshot_trigger]
  }
}

resource "scaleway_block_snapshot" "manual_data_volume" {
  for_each   = local.manual_snapshots_active
  project_id = var.project_id
  volume_id  = data.scaleway_block_volume.data_volume.id
  name       = format("devsh-k3s-%s-data-manual-%s", var.env_name, each.key)
  tags = [
    "devsh",
    "k3s",
    "snapshot",
    var.env_name,
    "managed=manual",
    "ttl_hours=${each.value.ttl_hours}",
  ]

  lifecycle {
    create_before_destroy = true
  }
}
