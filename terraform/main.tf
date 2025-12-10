terraform {
  required_version = ">= 1.5.0"

  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.60"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.10"
    }
  }
}

provider "scaleway" {
  zone   = "fr-par-1"
  region = "fr-par"
}

locals {
  env_raw           = trimspace(var.env_name)
  env_filtered      = regexall("[a-z0-9-]+", lower(local.env_raw))
  env_slug_cand     = length(local.env_filtered) > 0 ? join("-", local.env_filtered) : ""
  env_slug          = length(local.env_slug_cand) > 0 ? local.env_slug_cand : "prod"
  snapshot_enabled  = local.env_slug == "prod"
  domain_prefix     = local.env_slug == "prod" ? "" : "${local.env_slug}."
  kimai_domain      = format("%s%s", local.domain_prefix, trimspace(var.kimai_domain))
  monitoring_domain = format("%s%s", local.domain_prefix, trimspace(var.monitoring_domain))
  flux_hook_domain  = format("%s%s", local.domain_prefix, trimspace(var.flux_hook_domain))
}

module "k3s_node_prod" {
  source = "./modules/k3s_node"

  project_id                               = var.project_id
  env_name                                 = local.env_slug
  kimai_domain                             = local.kimai_domain
  monitoring_domain                        = local.monitoring_domain
  acme_email                               = var.acme_email
  config_repo_url                          = var.config_repo_url
  config_repo_branch                       = var.config_repo_branch
  config_repo_path                         = var.config_repo_path
  github_persistent_terra_infra_ro_pat     = var.github_persistent_terra_infra_ro_pat
  github_bootstrap_terra_infra_webhook_pat = var.github_bootstrap_terra_infra_webhook_pat
  flux_hook_domain                         = local.flux_hook_domain
  create_daily_snapshot                    = local.snapshot_enabled
  snapshot_rotation_hours                  = var.snapshot_rotation_hours

  cloud_init = templatefile("${path.root}/cloud-init.yaml", {
    acme_email                               = var.acme_email
    kimai_domain                             = local.kimai_domain
    monitoring_domain                        = local.monitoring_domain
    config_repo_url                          = var.config_repo_url
    config_repo_branch                       = var.config_repo_branch
    config_repo_path                         = var.config_repo_path
    github_persistent_terra_infra_ro_pat     = var.github_persistent_terra_infra_ro_pat
    github_bootstrap_terra_infra_webhook_pat = var.github_bootstrap_terra_infra_webhook_pat
    flux_hook_domain                         = local.flux_hook_domain
    env_name                                 = local.env_slug
    luks_key_access_key                      = var.luks_key_access_key
    luks_key_secret_key                      = var.luks_key_secret_key
    luks_key_url                             = var.luks_key_url
    path_root                                = path.root
    allow_fresh_bootstrap                    = var.allow_fresh_bootstrap
  })
}
