provider "scaleway" {
  organization_id = var.organization_id
  project_id      = var.project_id
  zone            = var.zone
  region          = var.region
}
