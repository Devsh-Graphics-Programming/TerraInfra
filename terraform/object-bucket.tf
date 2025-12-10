resource "scaleway_object_bucket" "luks_keys" {
  project_id = var.project_id
  region     = "fr-par"
  name       = "terra-luks-keys"

  lifecycle {
    prevent_destroy = true
  }
}

resource "scaleway_object_bucket_acl" "luks_keys" {
  bucket     = scaleway_object_bucket.luks_keys.id
  acl        = "private"
  region     = "fr-par"
  project_id = var.project_id
}
