output "volume_id" {
  value       = data.scaleway_block_volume.data_volume.id
  description = "ID of the snapshotted data volume"
}

output "latest_snapshot_id" {
  value       = scaleway_block_snapshot.data_volume.id
  description = "ID of the current managed daily snapshot"
}

