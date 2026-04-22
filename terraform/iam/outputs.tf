output "snapshots_application_id" {
  value       = scaleway_iam_application.snapshots.id
  description = "IAM application ID used by GitHub Actions snapshots"
}

output "snapshots_terraform_state_policy_id" {
  value       = scaleway_iam_policy.snapshots_terraform_state.id
  description = "IAM policy ID for Object Storage Terraform state access"
}

output "snapshots_block_storage_policy_id" {
  value       = scaleway_iam_policy.snapshots_block_storage.id
  description = "IAM policy ID for Block Storage snapshot permissions"
}

output "snapshots_instance_restore_drill_policy_id" {
  value       = scaleway_iam_policy.snapshots_instance_restore_drill.id
  description = "IAM policy ID for temporary restore drill instance permissions"
}

