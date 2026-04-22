output "volume_id" {
  value       = try(data.scaleway_block_volume.data_volume["node1-main"].id, "")
  description = "Legacy ID of the snapshotted node1 data volume"
  sensitive   = true
}

output "volume_ids" {
  value       = { for k, v in data.scaleway_block_volume.data_volume : k => v.id }
  description = "IDs of the snapshotted data volumes"
  sensitive   = true
}

output "volume_names" {
  value       = { for k, v in var.snapshot_targets : k => v.volume_name }
  description = "Names of the snapshotted data volumes"
  sensitive   = true
}

output "latest_snapshot_id" {
  value       = try(scaleway_block_snapshot.data_volume["node1-main"].id, "")
  description = "Legacy ID of the current managed daily node1 snapshot"
  sensitive   = true
}

output "latest_snapshot_name" {
  value       = try(scaleway_block_snapshot.data_volume["node1-main"].name, "")
  description = "Legacy name of the current managed auto node1 snapshot"
  sensitive   = true
}

output "latest_snapshot_ids" {
  value       = { for k, v in scaleway_block_snapshot.data_volume : k => v.id }
  description = "IDs of current managed daily snapshots by target"
  sensitive   = true
}

output "latest_snapshot_names" {
  value       = { for k, v in scaleway_block_snapshot.data_volume : k => v.name }
  description = "Names of current managed daily snapshots by target"
  sensitive   = true
}

output "auto_snapshot_trigger" {
  value       = time_static.snapshot_trigger.triggers.bucket
  description = "Current auto snapshot trigger value stored in state"
  sensitive   = true
}

output "manual_snapshots" {
  value       = local.manual_snapshots_active
  description = "Manual snapshot requests that are still active (used by CI to persist state)"
  sensitive   = true
}

output "manual_snapshot_ids" {
  value       = { for k, v in scaleway_block_snapshot.manual_data_volume : k => v.id }
  description = "IDs of active manual snapshots"
  sensitive   = true
}

output "manual_snapshot_names" {
  value       = { for k, v in scaleway_block_snapshot.manual_data_volume : k => v.name }
  description = "Names of active manual snapshots"
  sensitive   = true
}

output "manual_snapshot_request_keys" {
  value       = distinct([for k, v in scaleway_block_snapshot.manual_data_volume : split("/", k)[1]])
  description = "Manual snapshot request keys that still have managed snapshot resources"
  sensitive   = true
}

output "manual_snapshot_request_targets" {
  value = {
    for manual_key in distinct([for k, v in scaleway_block_snapshot.manual_data_volume : split("/", k)[1]]) :
    manual_key => sort([
      for resource_key, v in scaleway_block_snapshot.manual_data_volume :
      split("/", resource_key)[0]
      if split("/", resource_key)[1] == manual_key
    ])
  }
  description = "Manual snapshot request keys mapped to the target keys that still have managed snapshot resources"
  sensitive   = true
}

output "requested_manual_snapshot_id" {
  value       = try(scaleway_block_snapshot.manual_data_volume["node1-main/${var.requested_manual_snapshot_name}"].id, "")
  description = "Legacy ID of the requested node1 manual snapshot (when requested_manual_snapshot_name is set)"
  sensitive   = true
}

output "requested_manual_snapshot_name" {
  value       = try(scaleway_block_snapshot.manual_data_volume["node1-main/${var.requested_manual_snapshot_name}"].name, "")
  description = "Legacy name of the requested node1 manual snapshot (when requested_manual_snapshot_name is set)"
  sensitive   = true
}

output "requested_manual_snapshot_ids" {
  value = {
    for target_key in keys(var.snapshot_targets) :
    target_key => try(scaleway_block_snapshot.manual_data_volume["${target_key}/${var.requested_manual_snapshot_name}"].id, "")
  }
  description = "IDs of requested manual snapshots by target"
  sensitive   = true
}

output "requested_manual_snapshot_names" {
  value = {
    for target_key in keys(var.snapshot_targets) :
    target_key => try(scaleway_block_snapshot.manual_data_volume["${target_key}/${var.requested_manual_snapshot_name}"].name, "")
  }
  description = "Names of requested manual snapshots by target"
  sensitive   = true
}
