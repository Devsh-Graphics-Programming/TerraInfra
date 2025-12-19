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

locals {
  env_raw       = trimspace(var.env_name)
  env_filtered  = regexall("[a-z0-9-]+", lower(local.env_raw))
  env_slug_cand = length(local.env_filtered) > 0 ? join("-", local.env_filtered) : ""
  env_slug      = length(local.env_slug_cand) > 0 ? local.env_slug_cand : "prod"
}

module "k3s_node" {
  source = "./modules/k3s_node"

  project_id                               = var.project_id
  env_name                                 = local.env_slug
  acme_email                               = var.acme_email
  config_repo_url                          = var.config_repo_url
  config_repo_branch                       = var.config_repo_branch
  config_repo_path                         = var.config_repo_path
  github_persistent_terra_infra_ro_pat     = var.github_persistent_terra_infra_ro_pat
  github_bootstrap_terra_infra_webhook_pat = var.github_bootstrap_terra_infra_webhook_pat
  manual_snapshots                         = var.manual_snapshots
  prevent_destroy_data_volume              = var.prevent_destroy_data_volume
  data_volume_snapshot_id                  = var.data_volume_snapshot_id
  public_ip_id                             = var.public_ip_id
  public_ip_address                        = var.public_ip_address

  cloud_init = templatefile("${path.root}/cloud-init.yaml", {
    acme_email                               = var.acme_email
    config_repo_url                          = var.config_repo_url
    config_repo_branch                       = var.config_repo_branch
    config_repo_path                         = var.config_repo_path
    github_persistent_terra_infra_ro_pat     = var.github_persistent_terra_infra_ro_pat
    github_bootstrap_terra_infra_webhook_pat = var.github_bootstrap_terra_infra_webhook_pat
    env_name                                 = local.env_slug
    luks_key_access_key                      = var.luks_key_access_key
    luks_key_secret_key                      = var.luks_key_secret_key
    luks_key_url                             = var.luks_key_url
    sops_age_key                             = var.sops_age_key
    path_root                                = path.root
    allow_fresh_bootstrap                    = var.allow_fresh_bootstrap
    swap_file                                = var.swap_file
    swap_size_gb                             = var.swap_size_gb
    swap_swappiness                          = var.swap_swappiness
  })
}
