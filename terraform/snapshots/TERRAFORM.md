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
| <a name="input_manual_snapshots"></a> [manual\_snapshots](#input\_manual\_snapshots) | Manual snapshots (keyed by name) that are kept until ttl\_hours expires (requires terraform apply to enforce). | <pre>map(object({<br/>    created_at = string<br/>    ttl_hours  = optional(number, 24)<br/>    targets    = optional(list(string))<br/>  }))</pre> | `{}` | no |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Scaleway Project ID | `string` | n/a | yes |
| <a name="input_requested_manual_snapshot_name"></a> [requested\_manual\_snapshot\_name](#input\_requested\_manual\_snapshot\_name) | Optional manual snapshot key requested by the current run (used only for outputs/notifications). | `string` | `""` | no |
| <a name="input_snapshot_targets"></a> [snapshot\_targets](#input\_snapshot\_targets) | Block Volume snapshot targets keyed by stable logical name. | <pre>map(object({<br/>    volume_name = string<br/>    name_prefix = string<br/>    tags        = optional(list(string), [])<br/>  }))</pre> | <pre>{<br/>  "jenkins": {<br/>    "name_prefix": "jenkins-prod-data",<br/>    "tags": [<br/>      "jenkins",<br/>      "ci-controller"<br/>    ],<br/>    "volume_name": "jenkins-prod-data"<br/>  },<br/>  "node1-main": {<br/>    "name_prefix": "devsh-k3s-prod-data",<br/>    "tags": [<br/>      "node1",<br/>      "kimai"<br/>    ],<br/>    "volume_name": "devsh-k3s-prod-data-node1"<br/>  },<br/>  "observability": {<br/>    "name_prefix": "prod-observability-01-data",<br/>    "tags": [<br/>      "observability",<br/>      "monitoring"<br/>    ],<br/>    "volume_name": "prod-observability-01-data"<br/>  },<br/>  "rocket": {<br/>    "name_prefix": "prod-rocket-01-data",<br/>    "tags": [<br/>      "rocket",<br/>      "rocketchat"<br/>    ],<br/>    "volume_name": "prod-rocket-01-data"<br/>  },<br/>  "vpn-control": {<br/>    "name_prefix": "prod-vpn-control-01-data",<br/>    "tags": [<br/>      "vpn-control",<br/>      "headscale",<br/>      "authentik"<br/>    ],<br/>    "volume_name": "prod-vpn-control-01-data"<br/>  }<br/>}</pre> | no |
| <a name="input_volume_name"></a> [volume\_name](#input\_volume\_name) | Legacy single Block Volume name to snapshot. Use snapshot\_targets for new configuration. | `string` | `"devsh-k3s-prod-data-node1"` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_auto_snapshot_trigger"></a> [auto\_snapshot\_trigger](#output\_auto\_snapshot\_trigger) | Current auto snapshot trigger value stored in state |
| <a name="output_latest_snapshot_id"></a> [latest\_snapshot\_id](#output\_latest\_snapshot\_id) | Legacy ID of the current managed daily node1 snapshot |
| <a name="output_latest_snapshot_ids"></a> [latest\_snapshot\_ids](#output\_latest\_snapshot\_ids) | IDs of current managed daily snapshots by target |
| <a name="output_latest_snapshot_name"></a> [latest\_snapshot\_name](#output\_latest\_snapshot\_name) | Legacy name of the current managed auto node1 snapshot |
| <a name="output_latest_snapshot_names"></a> [latest\_snapshot\_names](#output\_latest\_snapshot\_names) | Names of current managed daily snapshots by target |
| <a name="output_manual_snapshot_ids"></a> [manual\_snapshot\_ids](#output\_manual\_snapshot\_ids) | IDs of active manual snapshots |
| <a name="output_manual_snapshot_names"></a> [manual\_snapshot\_names](#output\_manual\_snapshot\_names) | Names of active manual snapshots |
| <a name="output_manual_snapshot_request_keys"></a> [manual\_snapshot\_request\_keys](#output\_manual\_snapshot\_request\_keys) | Manual snapshot request keys that still have managed snapshot resources |
| <a name="output_manual_snapshot_request_targets"></a> [manual\_snapshot\_request\_targets](#output\_manual\_snapshot\_request\_targets) | Manual snapshot request keys mapped to the target keys that still have managed snapshot resources |
| <a name="output_manual_snapshots"></a> [manual\_snapshots](#output\_manual\_snapshots) | Manual snapshot requests that are still active (used by CI to persist state) |
| <a name="output_requested_manual_snapshot_id"></a> [requested\_manual\_snapshot\_id](#output\_requested\_manual\_snapshot\_id) | Legacy ID of the requested node1 manual snapshot (when requested\_manual\_snapshot\_name is set) |
| <a name="output_requested_manual_snapshot_ids"></a> [requested\_manual\_snapshot\_ids](#output\_requested\_manual\_snapshot\_ids) | IDs of requested manual snapshots by target |
| <a name="output_requested_manual_snapshot_name"></a> [requested\_manual\_snapshot\_name](#output\_requested\_manual\_snapshot\_name) | Legacy name of the requested node1 manual snapshot (when requested\_manual\_snapshot\_name is set) |
| <a name="output_requested_manual_snapshot_names"></a> [requested\_manual\_snapshot\_names](#output\_requested\_manual\_snapshot\_names) | Names of requested manual snapshots by target |
| <a name="output_volume_id"></a> [volume\_id](#output\_volume\_id) | Legacy ID of the snapshotted node1 data volume |
| <a name="output_volume_ids"></a> [volume\_ids](#output\_volume\_ids) | IDs of the snapshotted data volumes |
| <a name="output_volume_names"></a> [volume\_names](#output\_volume\_names) | Names of the snapshotted data volumes |
<!-- END_TF_DOCS -->
