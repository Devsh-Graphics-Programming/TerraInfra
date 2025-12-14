output "volume_id" {
  value       = data.scaleway_block_volume.data_volume.id
  description = "ID of the snapshotted data volume"
}

output "latest_snapshot_id" {
  value       = scaleway_block_snapshot.data_volume.id
  description = "ID of the current managed daily snapshot"
}

output "latest_snapshot_name" {
  value       = scaleway_block_snapshot.data_volume.name
  description = "Name of the current managed auto snapshot"
}

output "auto_snapshot_trigger" {
  value       = time_static.snapshot_trigger.triggers.bucket
  description = "Current auto snapshot trigger value stored in state"
}

output "manual_snapshots" {
  value       = local.manual_snapshots_active
  description = "Manual snapshot requests that are still active (used by CI to persist state)"
}

output "manual_snapshot_ids" {
  value       = { for k, v in scaleway_block_snapshot.manual_data_volume : k => v.id }
  description = "IDs of active manual snapshots"
}

output "requested_manual_snapshot_id" {
  value       = try(scaleway_block_snapshot.manual_data_volume[var.requested_manual_snapshot_name].id, "")
  description = "ID of the requested manual snapshot (when requested_manual_snapshot_name is set)"
}

output "requested_manual_snapshot_name" {
  value       = try(scaleway_block_snapshot.manual_data_volume[var.requested_manual_snapshot_name].name, "")
  description = "Name of the requested manual snapshot (when requested_manual_snapshot_name is set)"
}
