resource "scaleway_object_bucket" "luks_keys" {
  project_id = var.project_id
  region     = "fr-par"
  name       = "terra-luks-keys-${var.env_name}"
  acl        = "private"

  lifecycle {
    prevent_destroy = true
  }
}
