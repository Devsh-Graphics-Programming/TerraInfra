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
| [scaleway_block_snapshot.manual_data_volume](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/block_snapshot) | resource |
| [time_static.snapshot_trigger](https://registry.terraform.io/providers/hashicorp/time/latest/docs/resources/static) | resource |
| [scaleway_block_volume.data_volume](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/data-sources/block_volume) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_auto_snapshot_trigger"></a> [auto\_snapshot\_trigger](#input\_auto\_snapshot\_trigger) | Trigger value for the rotating (auto) snapshot. Change it to force a new auto snapshot. | `string` | n/a | yes |
| <a name="input_env_name"></a> [env\_name](#input\_env\_name) | Environment name used in snapshot naming | `string` | `"prod"` | no |
| <a name="input_manual_snapshots"></a> [manual\_snapshots](#input\_manual\_snapshots) | Manual snapshots (keyed by name) that are kept until ttl\_hours expires (requires terraform apply to enforce). | <pre>map(object({<br/>    created_at = string<br/>    ttl_hours  = optional(number, 24)<br/>  }))</pre> | `{}` | no |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Scaleway Project ID | `string` | n/a | yes |
| <a name="input_requested_manual_snapshot_name"></a> [requested\_manual\_snapshot\_name](#input\_requested\_manual\_snapshot\_name) | Optional manual snapshot key requested by the current run (used only for outputs/notifications). | `string` | `""` | no |
| <a name="input_volume_name"></a> [volume\_name](#input\_volume\_name) | Block Volume name to snapshot | `string` | `"devsh-k3s-prod-data-node1"` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_auto_snapshot_trigger"></a> [auto\_snapshot\_trigger](#output\_auto\_snapshot\_trigger) | Current auto snapshot trigger value stored in state |
| <a name="output_latest_snapshot_id"></a> [latest\_snapshot\_id](#output\_latest\_snapshot\_id) | ID of the current managed daily snapshot |
| <a name="output_latest_snapshot_name"></a> [latest\_snapshot\_name](#output\_latest\_snapshot\_name) | Name of the current managed auto snapshot |
| <a name="output_manual_snapshot_ids"></a> [manual\_snapshot\_ids](#output\_manual\_snapshot\_ids) | IDs of active manual snapshots |
| <a name="output_manual_snapshots"></a> [manual\_snapshots](#output\_manual\_snapshots) | Manual snapshot requests that are still active (used by CI to persist state) |
| <a name="output_requested_manual_snapshot_id"></a> [requested\_manual\_snapshot\_id](#output\_requested\_manual\_snapshot\_id) | ID of the requested manual snapshot (when requested\_manual\_snapshot\_name is set) |
| <a name="output_requested_manual_snapshot_name"></a> [requested\_manual\_snapshot\_name](#output\_requested\_manual\_snapshot\_name) | Name of the requested manual snapshot (when requested\_manual\_snapshot\_name is set) |
| <a name="output_volume_id"></a> [volume\_id](#output\_volume\_id) | ID of the snapshotted data volume |
<!-- END_TF_DOCS -->