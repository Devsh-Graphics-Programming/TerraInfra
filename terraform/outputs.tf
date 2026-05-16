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

output "jenkins_store_publisher_access_key" {
  value       = scaleway_iam_api_key.store_publisher.access_key
  description = "Access key for the Jenkins static store publisher"
  sensitive   = true
}

output "jenkins_store_publisher_secret_key" {
  value       = scaleway_iam_api_key.store_publisher.secret_key
  description = "Secret key for the Jenkins static store publisher"
  sensitive   = true
}

output "jenkins_node_ip" {
  value       = scaleway_instance_ip.jenkins.address
  description = "Public IP of the standalone Jenkins controller node"
}

output "jenkins_data_volume_id" {
  value       = scaleway_block_volume.jenkins_data.id
  description = "ID of the standalone Jenkins controller data volume"
}

output "observability_node_ip" {
  value       = scaleway_instance_ip.observability.address
  description = "Public IP of the dedicated observability node"
}

output "observability_data_volume_id" {
  value       = scaleway_block_volume.observability_data.id
  description = "ID of the dedicated observability data volume"
}

output "rocket_node_ip" {
  value       = scaleway_instance_ip.rocket.address
  description = "Public IP of the dedicated Rocket.Chat node"
}

output "rocket_data_volume_id" {
  value       = scaleway_block_volume.rocket_data.id
  description = "ID of the dedicated Rocket.Chat data volume"
}

output "vpn_control_node_ip" {
  value       = scaleway_instance_ip.vpn_control.address
  description = "Public IP of the dedicated VPN control-plane node"
}

output "vpn_control_data_volume_id" {
  value       = scaleway_block_volume.vpn_control_data.id
  description = "ID of the dedicated VPN control-plane data volume"
}
