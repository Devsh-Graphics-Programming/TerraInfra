output "public_ip" {
  value       = local.resolved_ip_address
  description = "Public IP of this k3s node"
}

output "server_id" {
  value       = scaleway_instance_server.k3s_node_1.id
  description = "Scaleway server ID"
}

output "data_volume_id" {
  value       = scaleway_block_volume.data_volume.id
  description = "ID of the attached data volume"
}

output "latest_snapshot_id" {
  value       = length(scaleway_block_snapshot.data_volume) > 0 ? scaleway_block_snapshot.data_volume[0].id : ""
  description = "ID of the most recent managed snapshot (empty when snapshots are disabled)"
}

output "manual_snapshot_ids" {
  value       = { for k, v in scaleway_block_snapshot.manual_data_volume : k => v.id }
  description = "IDs of managed manual snapshots"
}
