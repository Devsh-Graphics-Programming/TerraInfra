output "public_ip" {
  value       = scaleway_instance_ip.public_ip.address
  description = "Public IP of this k3s node"
}

output "server_id" {
  value       = scaleway_instance_server.k3s_node_1.id
  description = "Scaleway server ID"
}
