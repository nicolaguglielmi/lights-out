output "control_plane_ipv4" {
  value       = hcloud_server.control.ipv4_address
  description = "Public IPv4 of the Hetzner k3s API endpoint"
}

output "load_balancer_ipv4" {
  value       = hcloud_load_balancer.ingress.ipv4
  description = "Public IPv4 of the Hetzner Load Balancer fronting cluster ingress"
}

output "kube_apiserver_url" {
  value       = "https://${hcloud_server.control.ipv4_address}:6443"
  description = "Kubernetes API URL accepting OIDC bearer JWTs"
}

output "deployer_role" {
  value       = kubernetes_role.deployer.metadata[0].name
  description = "Least-privilege namespace Role bound to the OIDC subject"
}

output "monthly_budget_eur" {
  value       = var.monthly_budget_eur
  description = "Declared monthly Hetzner Cloud EUR budget cap (configure matching alert in Hetzner Console)"
}
