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

variable "target_key" {
  type        = string
  description = "Snapshot target key being restored"
}

variable "snapshot_id" {
  type        = string
  description = "Block snapshot ID used to create the temporary restore volume"
  sensitive   = true
}

variable "run_id" {
  type        = string
  description = "External run identifier used to name temporary resources"
}

variable "created_at" {
  type        = string
  description = "UTC timestamp tag used by janitor cleanup for temporary resources"
  default     = ""
}

variable "instance_type" {
  type        = string
  description = "Commercial type for the temporary restore verifier instance"
  default     = "DEV1-S"
}

variable "instance_image" {
  type        = string
  description = "Scaleway instance image label"
  default     = "debian_trixie"
}

variable "ssh_public_key" {
  type        = string
  description = "Ephemeral SSH public key allowed for the restore drill"
}

variable "ssh_cidr" {
  type        = string
  description = "CIDR allowed to connect to the temporary restore verifier over SSH"
  default     = "0.0.0.0/0"
}

variable "luks_key_access_key" {
  type        = string
  description = "Access key used by the temporary verifier to fetch the LUKS key"
  sensitive   = true
}

variable "luks_key_secret_key" {
  type        = string
  description = "Secret key used by the temporary verifier to fetch the LUKS key"
  sensitive   = true
}

variable "luks_key_url" {
  type        = string
  description = "Optional presigned URL to fetch the LUKS key"
  sensitive   = true
  default     = ""
}

variable "result_bucket_name" {
  type        = string
  description = "Object Storage bucket where the temporary verifier uploads sanitized status JSON"
  default     = "terra-snapshots-state"
}

variable "result_object_key" {
  type        = string
  description = "Object key where the temporary verifier uploads sanitized status JSON"
  default     = ""
}

variable "result_region" {
  type        = string
  description = "Object Storage region used for sanitized restore drill status upload"
  default     = "fr-par"
}

variable "result_endpoint" {
  type        = string
  description = "Object Storage endpoint used for sanitized restore drill status upload"
  default     = "https://s3.fr-par.scw.cloud"
}
