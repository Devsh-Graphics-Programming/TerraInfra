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
| [scaleway_block_volume.restore_drill](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/block_volume) | resource |
| [scaleway_instance_ip.restore_drill](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_ip) | resource |
| [scaleway_instance_security_group.restore_drill](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_security_group) | resource |
| [scaleway_instance_server.restore_drill](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_server) | resource |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_created_at"></a> [created\_at](#input\_created\_at) | UTC timestamp tag used by janitor cleanup for temporary resources | `string` | `""` | no |
| <a name="input_instance_image"></a> [instance\_image](#input\_instance\_image) | Scaleway instance image label | `string` | `"debian_trixie"` | no |
| <a name="input_instance_type"></a> [instance\_type](#input\_instance\_type) | Commercial type for the temporary restore verifier instance | `string` | `"DEV1-S"` | no |
| <a name="input_luks_key_access_key"></a> [luks\_key\_access\_key](#input\_luks\_key\_access\_key) | Access key used by the temporary verifier to fetch the LUKS key | `string` | n/a | yes |
| <a name="input_luks_key_secret_key"></a> [luks\_key\_secret\_key](#input\_luks\_key\_secret\_key) | Secret key used by the temporary verifier to fetch the LUKS key | `string` | n/a | yes |
| <a name="input_luks_key_url"></a> [luks\_key\_url](#input\_luks\_key\_url) | Optional presigned URL to fetch the LUKS key | `string` | `""` | no |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Scaleway Project ID | `string` | n/a | yes |
| <a name="input_region"></a> [region](#input\_region) | Scaleway region | `string` | `"fr-par"` | no |
| <a name="input_result_bucket_name"></a> [result\_bucket\_name](#input\_result\_bucket\_name) | Object Storage bucket where the temporary verifier uploads sanitized status JSON | `string` | `"terra-snapshots-state"` | no |
| <a name="input_result_endpoint"></a> [result\_endpoint](#input\_result\_endpoint) | Object Storage endpoint used for sanitized restore drill status upload | `string` | `"https://s3.fr-par.scw.cloud"` | no |
| <a name="input_result_object_key"></a> [result\_object\_key](#input\_result\_object\_key) | Object key where the temporary verifier uploads sanitized status JSON | `string` | `""` | no |
| <a name="input_result_region"></a> [result\_region](#input\_result\_region) | Object Storage region used for sanitized restore drill status upload | `string` | `"fr-par"` | no |
| <a name="input_run_id"></a> [run\_id](#input\_run\_id) | External run identifier used to name temporary resources | `string` | n/a | yes |
| <a name="input_snapshot_id"></a> [snapshot\_id](#input\_snapshot\_id) | Block snapshot ID used to create the temporary restore volume | `string` | n/a | yes |
| <a name="input_ssh_cidr"></a> [ssh\_cidr](#input\_ssh\_cidr) | CIDR allowed to connect to the temporary restore verifier over SSH | `string` | `"0.0.0.0/0"` | no |
| <a name="input_ssh_public_key"></a> [ssh\_public\_key](#input\_ssh\_public\_key) | Ephemeral SSH public key allowed for the restore drill | `string` | n/a | yes |
| <a name="input_target_key"></a> [target\_key](#input\_target\_key) | Snapshot target key being restored | `string` | n/a | yes |
| <a name="input_zone"></a> [zone](#input\_zone) | Scaleway zone | `string` | `"fr-par-1"` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_server_id"></a> [server\_id](#output\_server\_id) | Temporary restore verifier server ID |
| <a name="output_server_ip"></a> [server\_ip](#output\_server\_ip) | Temporary restore verifier public IP |
| <a name="output_volume_id"></a> [volume\_id](#output\_volume\_id) | Temporary restore verifier data volume ID |
<!-- END_TF_DOCS -->
