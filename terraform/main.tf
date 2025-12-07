terraform {
  required_version = ">= 1.5.0"

  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.60"
    }
  }
}

provider "scaleway" {
  zone   = "fr-par-1"
  region = "fr-par"
}

module "k3s_node_prod" {
  source = "./modules/k3s_node"

  project_id        = var.project_id
  env_name          = var.env_name
  kimai_domain      = var.kimai_domain
  monitoring_domain = var.monitoring_domain
  acme_email        = var.acme_email
  config_repo_url   = var.config_repo_url
  config_repo_branch = var.config_repo_branch
  config_repo_path  = var.config_repo_path
  github_persistent_terra_infra_ro_pat  = var.github_persistent_terra_infra_ro_pat
  github_bootstrap_terra_infra_webhook_pat = var.github_bootstrap_terra_infra_webhook_pat
  flux_hook_domain  = var.flux_hook_domain

  cloud_init = templatefile("${path.root}/cloud-init.yaml", {
    acme_email        = var.acme_email
    kimai_domain      = var.kimai_domain
    monitoring_domain = var.monitoring_domain
    config_repo_url   = var.config_repo_url
    config_repo_branch = var.config_repo_branch
  config_repo_path  = var.config_repo_path
  github_persistent_terra_infra_ro_pat  = var.github_persistent_terra_infra_ro_pat
  github_bootstrap_terra_infra_webhook_pat = var.github_bootstrap_terra_infra_webhook_pat
  flux_hook_domain  = var.flux_hook_domain
  path_root         = path.root
  })
}
