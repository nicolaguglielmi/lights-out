terraform {
  required_version = ">= 1.5"
  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.49"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.31"
    }
  }
}

provider "hcloud" {}

resource "hcloud_network" "k3s" {
  name     = "${var.cluster_name}-net"
  ip_range = "10.42.0.0/16"
}

resource "hcloud_network_subnet" "nodes" {
  network_id   = hcloud_network.k3s.id
  type         = "cloud"
  network_zone = "eu-central"
  ip_range     = "10.42.1.0/24"
}

resource "hcloud_firewall" "k3s" {
  name = "${var.cluster_name}-fw"

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "6443"
    source_ips = var.allowed_api_cidrs
  }

  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
}

locals {
  # Configure k3s kube-apiserver with OIDC JWT verification so CI jobs present
  # short-lived OIDC tokens instead of long-lived static cluster client certs.
  k3s_cloud_init = <<-EOT
    #cloud-config
    runcmd:
      - |
        curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server \
          --kube-apiserver-arg=oidc-issuer-url=${var.oidc_issuer_url} \
          --kube-apiserver-arg=oidc-client-id=${var.oidc_client_id} \
          --kube-apiserver-arg=oidc-username-claim=sub \
          --kube-apiserver-arg=oidc-username-prefix=oidc:" sh -
  EOT
}

resource "hcloud_server" "control" {
  name         = "${var.cluster_name}-cp-1"
  image        = "ubuntu-24.04"
  server_type  = var.server_type
  location     = var.location
  firewall_ids = [hcloud_firewall.k3s.id]
  user_data    = local.k3s_cloud_init

  network {
    network_id = hcloud_network.k3s.id
    ip         = "10.42.1.10"
  }

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  labels = {
    managed_by  = "lights-out"
    budget_eur  = tostring(var.monthly_budget_eur)
    oidc_issuer = replace(var.oidc_issuer_url, "/[^a-zA-Z0-9_-]/", "-")
  }

  depends_on = [hcloud_network_subnet.nodes]
}

resource "hcloud_load_balancer" "ingress" {
  name               = "${var.cluster_name}-lb"
  load_balancer_type = "lb11"
  location           = var.location
}

resource "hcloud_load_balancer_network" "ingress" {
  load_balancer_id = hcloud_load_balancer.ingress.id
  network_id       = hcloud_network.k3s.id
  ip               = "10.42.1.2"
}

resource "hcloud_load_balancer_target" "control" {
  type             = "server"
  load_balancer_id = hcloud_load_balancer.ingress.id
  server_id        = hcloud_server.control.id
  use_private_ip   = true
}

resource "kubernetes_namespace" "delivery" {
  metadata {
    name = var.namespace
  }
}

# Least-privilege namespace Role for Argo Rollouts + canary pod/ingress reads.
resource "kubernetes_role" "deployer" {
  metadata {
    name      = "${var.cluster_name}-deployer"
    namespace = kubernetes_namespace.delivery.metadata[0].name
  }

  rule {
    api_groups = ["argoproj.io"]
    resources  = ["rollouts", "rollouts/status"]
    verbs      = ["get", "list", "watch", "patch", "update"]
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "pods/proxy", "services"]
    verbs      = ["get", "list"]
  }

  rule {
    api_groups = ["networking.k8s.io"]
    resources  = ["ingresses"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_role_binding" "oidc_deployer" {
  metadata {
    name      = "${var.cluster_name}-oidc-deployer"
    namespace = kubernetes_namespace.delivery.metadata[0].name
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.deployer.metadata[0].name
  }

  subject {
    kind      = "User"
    name      = "oidc:${var.oidc_subject}"
    api_group = "rbac.authorization.k8s.io"
  }
}
