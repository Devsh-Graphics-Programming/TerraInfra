output "k3s_node_1_ip" {
  value       = module.k3s_node_prod.public_ip
  description = "Public IP of k3s node"
}
