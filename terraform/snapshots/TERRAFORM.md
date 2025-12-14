<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.5.0 |
| <a name="requirement_scaleway"></a> [scaleway](#requirement\_scaleway) | ~> 2.60 |
| <a name="requirement_time"></a> [time](#requirement\_time) | ~> 0.10 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_scaleway"></a> [scaleway](#provider\_scaleway) | ~> 2.60 |
| <a name="provider_time"></a> [time](#provider\_time) | ~> 0.10 |

## Modules

No modules.

## Resources

| Name | Type |
|------|------|
| [scaleway_block_snapshot.data_volume](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/block_snapshot) | resource |
| [time_static.snapshot_trigger](https://registry.terraform.io/providers/hashicorp/time/latest/docs/resources/static) | resource |
| [scaleway_block_volume.data_volume](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/data-sources/block_volume) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_env_name"></a> [env\_name](#input\_env\_name) | Environment name used in snapshot naming | `string` | `"prod"` | no |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Scaleway Project ID | `string` | n/a | yes |
| <a name="input_volume_name"></a> [volume\_name](#input\_volume\_name) | Block Volume name to snapshot | `string` | `"devsh-k3s-prod-data-node1"` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_latest_snapshot_id"></a> [latest\_snapshot\_id](#output\_latest\_snapshot\_id) | ID of the current managed daily snapshot |
| <a name="output_volume_id"></a> [volume\_id](#output\_volume\_id) | ID of the snapshotted data volume |
<!-- END_TF_DOCS -->