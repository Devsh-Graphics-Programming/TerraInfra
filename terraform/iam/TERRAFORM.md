<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.5.0 |
| <a name="requirement_scaleway"></a> [scaleway](#requirement\_scaleway) | ~> 2.60 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_scaleway"></a> [scaleway](#provider\_scaleway) | ~> 2.60 |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [scaleway_iam_application.snapshots](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/iam_application) | resource |
| [scaleway_iam_policy.snapshots_block_storage](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/iam_policy) | resource |
| [scaleway_iam_policy.snapshots_terraform_state](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/iam_policy) | resource |
| [scaleway_object_bucket_policy.luks_keys](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/object_bucket_policy) | resource |
| [scaleway_object_bucket_policy.snapshots_state](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/object_bucket_policy) | resource |
| [scaleway_iam_api_key.luks_reader](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/data-sources/iam_api_key) | data source |
| [scaleway_iam_api_key.owner](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/data-sources/iam_api_key) | data source |
| [scaleway_iam_user.owner](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/data-sources/iam_user) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_block_storage_permission_set_names"></a> [block\_storage\_permission\_set\_names](#input\_block\_storage\_permission\_set\_names) | IAM permission sets for managing block storage snapshots | `list(string)` | <pre>[<br/>  "BlockStorageFullAccess"<br/>]</pre> | no |
| <a name="input_luks_bucket_name"></a> [luks\_bucket\_name](#input\_luks\_bucket\_name) | Object Storage bucket name holding the LUKS key | `string` | `"terra-luks-keys"` | no |
| <a name="input_luks_key_access_key"></a> [luks\_key\_access\_key](#input\_luks\_key\_access\_key) | Access key of the LUKS reader (used to resolve application/user automatically) | `string` | `""` | no |
| <a name="input_luks_key_object_name"></a> [luks\_key\_object\_name](#input\_luks\_key\_object\_name) | Object name inside luks\_bucket\_name containing the LUKS key | `string` | `"luks.key"` | no |
| <a name="input_luks_reader_application_id"></a> [luks\_reader\_application\_id](#input\_luks\_reader\_application\_id) | IAM application ID allowed to read the LUKS key (optional if luks\_key\_access\_key is set) | `string` | `""` | no |
| <a name="input_luks_reader_user_id"></a> [luks\_reader\_user\_id](#input\_luks\_reader\_user\_id) | IAM user ID allowed to read the LUKS key (optional if luks\_key\_access\_key is set) | `string` | `""` | no |
| <a name="input_manage_luks_bucket_policy"></a> [manage\_luks\_bucket\_policy](#input\_manage\_luks\_bucket\_policy) | Whether to manage the LUKS bucket policy (recommended) | `bool` | `true` | no |
| <a name="input_object_storage_permission_set_names"></a> [object\_storage\_permission\_set\_names](#input\_object\_storage\_permission\_set\_names) | IAM permission sets for managing the snapshots Terraform state bucket | `list(string)` | <pre>[<br/>  "ObjectStorageBucketsRead",<br/>  "ObjectStorageObjectsRead",<br/>  "ObjectStorageObjectsWrite",<br/>  "ObjectStorageObjectsDelete"<br/>]</pre> | no |
| <a name="input_organization_id"></a> [organization\_id](#input\_organization\_id) | Scaleway Organization ID (required only for some IAM lookups) | `string` | `null` | no |
| <a name="input_owner_access_key"></a> [owner\_access\_key](#input\_owner\_access\_key) | Scaleway access key used to resolve owner\_user\_id automatically | `string` | `""` | no |
| <a name="input_owner_user_email"></a> [owner\_user\_email](#input\_owner\_user\_email) | Scaleway IAM User email to resolve owner\_user\_id | `string` | `""` | no |
| <a name="input_owner_user_id"></a> [owner\_user\_id](#input\_owner\_user\_id) | Scaleway IAM User ID to keep full bucket access | `string` | `""` | no |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Scaleway Project ID | `string` | n/a | yes |
| <a name="input_region"></a> [region](#input\_region) | Scaleway region | `string` | `"fr-par"` | no |
| <a name="input_snapshots_application_description"></a> [snapshots\_application\_description](#input\_snapshots\_application\_description) | IAM application description | `string` | `"GitHub Actions application managing Terraform snapshots"` | no |
| <a name="input_snapshots_application_name"></a> [snapshots\_application\_name](#input\_snapshots\_application\_name) | IAM application name used by GitHub Actions snapshots | `string` | `"actions-snapshot-manager"` | no |
| <a name="input_snapshots_state_bucket_name"></a> [snapshots\_state\_bucket\_name](#input\_snapshots\_state\_bucket\_name) | Object Storage bucket name holding Terraform state for snapshots | `string` | `"terra-snapshots-state"` | no |
| <a name="input_snapshots_state_object_prefix"></a> [snapshots\_state\_object\_prefix](#input\_snapshots\_state\_object\_prefix) | Object key prefix in the state bucket used by the snapshots Terraform backend | `string` | `"terraform/snapshots/"` | no |
| <a name="input_zone"></a> [zone](#input\_zone) | Scaleway zone | `string` | `"fr-par-1"` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_snapshots_application_id"></a> [snapshots\_application\_id](#output\_snapshots\_application\_id) | IAM application ID used by GitHub Actions snapshots |
| <a name="output_snapshots_block_storage_policy_id"></a> [snapshots\_block\_storage\_policy\_id](#output\_snapshots\_block\_storage\_policy\_id) | IAM policy ID for Block Storage snapshot permissions |
| <a name="output_snapshots_terraform_state_policy_id"></a> [snapshots\_terraform\_state\_policy\_id](#output\_snapshots\_terraform\_state\_policy\_id) | IAM policy ID for Object Storage Terraform state access |
<!-- END_TF_DOCS -->