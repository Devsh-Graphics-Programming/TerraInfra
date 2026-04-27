locals {
  store_bucket_name = "devsh-store-${local.env_slug}"
  store_bucket_host = "${local.store_bucket_name}.s3.fr-par.scw.cloud"
}

data "scaleway_account_project" "current" {
  project_id = var.project_id
}

resource "scaleway_iam_application" "store_publisher" {
  name            = "jenkins-store-publisher-${local.env_slug}"
  description     = "Jenkins publisher for static DITT smoke reports"
  organization_id = data.scaleway_account_project.current.organization_id
  tags            = ["devsh", local.env_slug, "jenkins", "store"]
}

resource "scaleway_iam_policy" "store_publisher_object_storage" {
  name            = "jenkins-store-publisher-${local.env_slug}"
  description     = "Object Storage write access for Jenkins static report publishing"
  application_id  = scaleway_iam_application.store_publisher.id
  organization_id = scaleway_iam_application.store_publisher.organization_id

  rule {
    project_ids = [var.project_id]
    permission_set_names = [
      "ObjectStorageBucketsRead",
      "ObjectStorageObjectsRead",
      "ObjectStorageObjectsDelete",
      "ObjectStorageObjectsWrite",
    ]
  }
}

resource "scaleway_iam_api_key" "store_publisher" {
  application_id     = scaleway_iam_application.store_publisher.id
  default_project_id = var.project_id
  description        = "Jenkins static store publisher"
}

data "scaleway_iam_api_key" "store_owner" {
  count      = var.owner_access_key != "" ? 1 : 0
  access_key = var.owner_access_key
}

locals {
  store_owner_application_id = var.owner_access_key != "" && try(data.scaleway_iam_api_key.store_owner[0].application_id, null) != null ? data.scaleway_iam_api_key.store_owner[0].application_id : ""
  store_owner_user_id        = var.owner_access_key != "" && try(data.scaleway_iam_api_key.store_owner[0].user_id, null) != null ? data.scaleway_iam_api_key.store_owner[0].user_id : ""
  store_owner_principal      = local.store_owner_application_id != "" ? "application_id:${local.store_owner_application_id}" : "user_id:${local.store_owner_user_id}"
}

resource "scaleway_object_bucket" "store" {
  project_id = var.project_id
  region     = "fr-par"
  name       = local.store_bucket_name

  tags = {
    Service = "store"
    Env     = local.env_slug
  }
}

resource "scaleway_object_bucket_policy" "store" {
  bucket     = scaleway_object_bucket.store.id
  project_id = var.project_id
  policy = jsonencode({
    Version = "2023-04-17",
    Id      = "store-bucket-policy",
    Statement = [
      {
        Sid    = "ProjectFullAccess"
        Effect = "Allow"
        Principal = {
          SCW = local.store_owner_principal
        }
        Action = [
          "s3:*",
        ]
        Resource = [
          scaleway_object_bucket.store.name,
          "${scaleway_object_bucket.store.name}/*",
        ]
      },
      {
        Sid    = "AllowStoreProxyRead"
        Effect = "Allow"
        Principal = {
          SCW = "*"
        }
        Action = [
          "s3:GetObject",
        ]
        Resource = [
          "${scaleway_object_bucket.store.name}/*",
        ]
        Condition = {
          IpAddress = {
            "aws:SourceIp" = "${module.k3s_node.public_ip}/32"
          }
        }
      },
      {
        Sid    = "AllowJenkinsDittReportObjectAccess"
        Effect = "Allow"
        Principal = {
          SCW = "application_id:${scaleway_iam_application.store_publisher.id}"
        }
        Action = [
          "s3:ListBucket",
          "s3:DeleteObject",
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = [
          scaleway_object_bucket.store.name,
          "${scaleway_object_bucket.store.name}/ditt/dummy/*",
          "${scaleway_object_bucket.store.name}/ditt/public/*",
          "${scaleway_object_bucket.store.name}/ditt/private/*",
          "${scaleway_object_bucket.store.name}/ditt/compare/*",
        ]
      },
    ]
  })

  lifecycle {
    precondition {
      condition     = local.store_owner_user_id != "" || local.store_owner_application_id != ""
      error_message = "Set owner_access_key to resolve the Object Storage bucket policy owner principal."
    }
  }
}
