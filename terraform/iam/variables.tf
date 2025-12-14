variable "project_id" {
  type        = string
  description = "Scaleway Project ID"
}

variable "region" {
  type        = string
  description = "Scaleway region"
  default     = "fr-par"
}

variable "zone" {
  type        = string
  description = "Scaleway zone"
  default     = "fr-par-1"
}

variable "owner_user_id" {
  type        = string
  description = "Scaleway IAM User ID to keep full bucket access"
  default     = ""
}

variable "owner_user_email" {
  type        = string
  description = "Scaleway IAM User email to resolve owner_user_id"
  default     = ""

  validation {
    condition     = var.owner_user_id != "" || var.owner_user_email != ""
    error_message = "Set either owner_user_id or owner_user_email."
  }
}

variable "snapshots_application_name" {
  type        = string
  description = "IAM application name used by GitHub Actions snapshots"
  default     = "actions-snapshot-manager"
}

variable "snapshots_application_description" {
  type        = string
  description = "IAM application description"
  default     = "GitHub Actions application managing Terraform snapshots"
}

variable "snapshots_state_bucket_name" {
  type        = string
  description = "Object Storage bucket name holding Terraform state for snapshots"
  default     = "terra-snapshots-state"
}

variable "snapshots_state_object_prefix" {
  type        = string
  description = "Object key prefix in the state bucket used by the snapshots Terraform backend"
  default     = "terraform/snapshots/"
}

variable "luks_bucket_name" {
  type        = string
  description = "Object Storage bucket name holding the LUKS key"
  default     = "terra-luks-keys"
}

variable "luks_key_object_name" {
  type        = string
  description = "Object name inside luks_bucket_name containing the LUKS key"
  default     = "luks.key"
}

variable "luks_reader_application_id" {
  type        = string
  description = "IAM application ID allowed to read the LUKS key (optional if luks_key_access_key is set)"
  default     = ""
}

variable "luks_reader_user_id" {
  type        = string
  description = "IAM user ID allowed to read the LUKS key (optional if luks_key_access_key is set)"
  default     = ""
}

variable "luks_key_access_key" {
  type        = string
  description = "Access key of the LUKS reader (used to resolve application/user automatically)"
  sensitive   = true
  default     = ""
}

variable "manage_luks_bucket_policy" {
  type        = bool
  description = "Whether to manage the LUKS bucket policy (recommended)"
  default     = true
}

variable "object_storage_permission_set_names" {
  type        = list(string)
  description = "IAM permission sets for managing the snapshots Terraform state bucket"
  default = [
    "ObjectStorageBucketsRead",
    "ObjectStorageObjectsRead",
    "ObjectStorageObjectsWrite",
    "ObjectStorageObjectsDelete",
  ]
}

variable "block_storage_permission_set_names" {
  type        = list(string)
  description = "IAM permission sets for managing block storage snapshots"
  default     = ["BlockStorageFullAccess"]
}
