output "server_ip" {
  value       = scaleway_instance_ip.restore_drill.address
  description = "Temporary restore verifier public IP"
  sensitive   = true
}

output "server_id" {
  value       = scaleway_instance_server.restore_drill.id
  description = "Temporary restore verifier server ID"
  sensitive   = true
}

output "volume_id" {
  value       = scaleway_block_volume.restore_drill.id
  description = "Temporary restore verifier data volume ID"
  sensitive   = true
}
