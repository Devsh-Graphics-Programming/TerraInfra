output "k3s_node_1_ip" {
  value       = module.k3s_node.public_ip
  description = "Public IP of k3s node"
}

output "data_volume_id" {
  value       = module.k3s_node.data_volume_id
  description = "ID of the environment's data volume"
}

output "latest_snapshot_id" {
  value       = module.k3s_node.latest_snapshot_id
  description = "Most recent managed snapshot ID (empty when snapshots are disabled)"
}
