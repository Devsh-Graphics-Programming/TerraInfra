data "scaleway_block_volume" "data_volume" {
  name       = var.volume_name
  project_id = var.project_id
}

resource "time_static" "snapshot_trigger" {
  triggers = {
    bucket = formatdate("YYYY-MM-DD", plantimestamp())
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
    "managed=daily",
  ]

  lifecycle {
    create_before_destroy = true
    replace_triggered_by  = [time_static.snapshot_trigger]
  }
}

