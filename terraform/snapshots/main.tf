data "scaleway_block_volume" "data_volume" {
  for_each   = var.snapshot_targets
  name       = each.value.volume_name
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
      targets    = try(cfg.targets, keys(var.snapshot_targets))
    }
    if timecmp(plantimestamp(), timeadd(cfg.created_at, format("%dh", try(cfg.ttl_hours, 24)))) == -1
  }

  snapshot_timestamp = replace(
    replace(
      replace(time_static.snapshot_trigger.rfc3339, ":", "-"),
      "T",
      "-"
    ),
    "Z",
    ""
  )

  manual_snapshot_targets = merge([
    for manual_key, cfg in local.manual_snapshots_active : {
      for target_key in cfg.targets :
      "${target_key}/${manual_key}" => {
        target_key = target_key
        manual_key = manual_key
        ttl_hours  = cfg.ttl_hours
      }
      if contains(keys(var.snapshot_targets), target_key)
    }
  ]...)
}

resource "scaleway_block_snapshot" "data_volume" {
  for_each   = var.snapshot_targets
  project_id = var.project_id
  volume_id  = data.scaleway_block_volume.data_volume[each.key].id
  name       = format("%s-snapshot-%s", each.value.name_prefix, local.snapshot_timestamp)
  tags = concat([
    "devsh",
    "snapshot",
    var.env_name,
    "managed=auto",
    "target=${each.key}",
  ], each.value.tags)

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [volume_id]
    replace_triggered_by  = [time_static.snapshot_trigger]
  }
}

moved {
  from = scaleway_block_snapshot.data_volume
  to   = scaleway_block_snapshot.data_volume["node1-main"]
}

resource "scaleway_block_snapshot" "manual_data_volume" {
  for_each   = local.manual_snapshot_targets
  project_id = var.project_id
  volume_id  = data.scaleway_block_volume.data_volume[each.value.target_key].id
  name       = format("%s-manual-%s", var.snapshot_targets[each.value.target_key].name_prefix, each.value.manual_key)
  tags = concat([
    "devsh",
    "snapshot",
    var.env_name,
    "managed=manual",
    "target=${each.value.target_key}",
    "ttl_hours=${each.value.ttl_hours}",
  ], var.snapshot_targets[each.value.target_key].tags)

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [volume_id]
  }

  depends_on = [scaleway_block_snapshot.data_volume]
}
