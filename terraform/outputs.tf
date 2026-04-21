output "k3s_node_1_ip" {
  value       = module.k3s_node.public_ip
  description = "Public IP of k3s node"
}

output "data_volume_id" {
  value       = module.k3s_node.data_volume_id
  description = "ID of the environment's data volume"
}

output "manual_snapshot_ids" {
  value       = module.k3s_node.manual_snapshot_ids
  description = "IDs of managed manual snapshots (empty when none are configured)"
}

output "store_bucket_name" {
  value       = scaleway_object_bucket.store.name
  description = "Object Storage bucket used by the static store proxy"
}

output "store_bucket_host" {
  value       = local.store_bucket_host
  description = "Object Storage host used by the static store proxy"
}
