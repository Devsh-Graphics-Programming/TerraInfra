data "scaleway_iam_api_key" "luks_reader" {
  count      = var.luks_key_access_key != "" ? 1 : 0
  access_key = var.luks_key_access_key
}

data "scaleway_iam_api_key" "owner" {
  count      = var.owner_user_id == "" && var.owner_access_key != "" ? 1 : 0
  access_key = var.owner_access_key
}

data "scaleway_iam_user" "owner" {
  count = var.owner_user_id == "" && var.owner_access_key == "" && var.owner_user_email != "" ? 1 : 0
  email = var.owner_user_email
}

locals {
  owner_user_id = var.owner_user_id != "" ? var.owner_user_id : (
    var.owner_access_key != "" ? data.scaleway_iam_api_key.owner[0].user_id : (
      var.owner_user_email != "" ? data.scaleway_iam_user.owner[0].id : ""
    )
  )

  state_object_prefix_trimmed = trim(var.snapshots_state_object_prefix, "/")
  state_object_resource = local.state_object_prefix_trimmed != "" ? format(
    "%s/%s/*",
    var.snapshots_state_bucket_name,
    local.state_object_prefix_trimmed
    ) : format(
    "%s/*",
    var.snapshots_state_bucket_name
  )

  luks_object_resource = format(
    "%s/%s",
    var.luks_bucket_name,
    trim(var.luks_key_object_name, "/")
  )

  luks_reader_application_id = var.luks_reader_application_id != "" ? var.luks_reader_application_id : (
    var.luks_key_access_key != "" ? data.scaleway_iam_api_key.luks_reader[0].application_id : ""
  )
  luks_reader_user_id = var.luks_reader_user_id != "" ? var.luks_reader_user_id : (
    var.luks_key_access_key != "" ? data.scaleway_iam_api_key.luks_reader[0].user_id : ""
  )
  luks_reader_principal = local.luks_reader_application_id != "" ? "application_id:${local.luks_reader_application_id}" : (
    local.luks_reader_user_id != "" ? "user_id:${local.luks_reader_user_id}" : ""
  )
}

resource "scaleway_iam_application" "snapshots" {
  name        = var.snapshots_application_name
  description = var.snapshots_application_description
}

resource "scaleway_iam_policy" "snapshots_terraform_state" {
  name            = "snapshots-terra-state-accessor"
  description     = "Access to the dedicated Object Storage bucket holding Terraform state for snapshots"
  application_id  = scaleway_iam_application.snapshots.id
  organization_id = scaleway_iam_application.snapshots.organization_id

  rule {
    project_ids          = [var.project_id]
    permission_set_names = var.object_storage_permission_set_names
  }
}

resource "scaleway_iam_policy" "snapshots_block_storage" {
  name            = "snapshots-block-storage-reader"
  description     = "Block Storage permissions required to create/delete snapshots"
  application_id  = scaleway_iam_application.snapshots.id
  organization_id = scaleway_iam_application.snapshots.organization_id

  rule {
    project_ids          = [var.project_id]
    permission_set_names = var.block_storage_permission_set_names
  }
}

resource "scaleway_iam_policy" "snapshots_instance_restore_drill" {
  name            = "snapshots-instance-restore-drill"
  description     = "Instance permissions required to run temporary snapshot restore drill verifiers"
  application_id  = scaleway_iam_application.snapshots.id
  organization_id = scaleway_iam_application.snapshots.organization_id

  rule {
    project_ids          = [var.project_id]
    permission_set_names = var.instance_permission_set_names
  }
}

resource "scaleway_object_bucket_policy" "snapshots_state" {
  bucket = var.snapshots_state_bucket_name
  policy = jsonencode({
    Version = "2023-04-17",
    Id      = "snapshots-state-bucket-policy",
    Statement = [
      {
        Sid    = "OwnerFullAccess"
        Effect = "Allow"
        Principal = {
          SCW = "user_id:${local.owner_user_id}"
        }
        Action = ["s3:*"]
        Resource = [
          var.snapshots_state_bucket_name,
          "${var.snapshots_state_bucket_name}/*",
        ]
      },
      {
        Sid    = "SnapshotsAppTerraformState"
        Effect = "Allow"
        Principal = {
          SCW = "application_id:${scaleway_iam_application.snapshots.id}"
        }
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
        ]
        Resource = [
          var.snapshots_state_bucket_name,
          local.state_object_resource,
        ]
      },
    ]
  })

  lifecycle {
    precondition {
      condition     = local.owner_user_id != ""
      error_message = "Could not resolve owner_user_id. Set owner_user_id, owner_access_key (user key), or owner_user_email (requires organization_id)."
    }
  }
}

resource "scaleway_object_bucket_policy" "luks_keys" {
  count  = var.manage_luks_bucket_policy ? 1 : 0
  bucket = var.luks_bucket_name
  policy = jsonencode({
    Version = "2023-04-17",
    Id      = "luks-keys-bucket-policy",
    Statement = [
      {
        Sid    = "OwnerFullAccess"
        Effect = "Allow"
        Principal = {
          SCW = "user_id:${local.owner_user_id}"
        }
        Action = ["s3:*"]
        Resource = [
          var.luks_bucket_name,
          "${var.luks_bucket_name}/*",
        ]
      },
      {
        Sid    = "LuksReaderAccess"
        Effect = "Allow"
        Principal = {
          SCW = local.luks_reader_principal
        }
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "s3:GetObject",
        ]
        Resource = [
          var.luks_bucket_name,
          local.luks_object_resource,
        ]
      },
    ]
  })

  lifecycle {
    precondition {
      condition     = local.luks_reader_principal != ""
      error_message = "Provide luks_reader_application_id or luks_reader_user_id, or set luks_key_access_key, when manage_luks_bucket_policy=true."
    }
  }
}
