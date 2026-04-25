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

| Name | Source | Version |
|------|--------|---------|
| <a name="module_k3s_node"></a> [k3s\_node](#module\_k3s\_node) | ./modules/k3s_node | n/a |

## Resources

| Name | Type |
|------|------|
| [scaleway_block_volume.chat_data](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/block_volume) | resource |
| [scaleway_block_volume.jenkins_data](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/block_volume) | resource |
| [scaleway_block_volume.observability_data](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/block_volume) | resource |
| [scaleway_iam_api_key.store_publisher](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/iam_api_key) | resource |
| [scaleway_iam_application.store_publisher](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/iam_application) | resource |
| [scaleway_iam_policy.store_publisher_object_storage](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/iam_policy) | resource |
| [scaleway_instance_ip.chat](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_ip) | resource |
| [scaleway_instance_ip.jenkins](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_ip) | resource |
| [scaleway_instance_ip.observability](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_ip) | resource |
| [scaleway_instance_security_group.chat](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_security_group) | resource |
| [scaleway_instance_security_group.jenkins](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_security_group) | resource |
| [scaleway_instance_security_group.observability](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_security_group) | resource |
| [scaleway_instance_server.chat](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_server) | resource |
| [scaleway_instance_server.jenkins](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_server) | resource |
| [scaleway_instance_server.observability](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/instance_server) | resource |
| [scaleway_object_bucket.luks_keys](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/object_bucket) | resource |
| [scaleway_object_bucket.store](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/object_bucket) | resource |
| [scaleway_object_bucket_acl.luks_keys](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/object_bucket_acl) | resource |
| [scaleway_object_bucket_policy.store](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/resources/object_bucket_policy) | resource |
| [scaleway_account_project.current](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/data-sources/account_project) | data source |
| [scaleway_iam_api_key.store_owner](https://registry.terraform.io/providers/scaleway/scaleway/latest/docs/data-sources/iam_api_key) | data source |

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_acme_email"></a> [acme\_email](#input\_acme\_email) | Email for ACME | `string` | n/a | yes |
| <a name="input_allow_fresh_bootstrap"></a> [allow\_fresh\_bootstrap](#input\_allow\_fresh\_bootstrap) | Set to true when you intentionally want to recreate secrets (new volume or clean data). | `bool` | `false` | no |
| <a name="input_chat_data_volume_size_gb"></a> [chat\_data\_volume\_size\_gb](#input\_chat\_data\_volume\_size\_gb) | Size of the dedicated StoatChat data volume in GiB. | `number` | `10` | no |
| <a name="input_chat_instance_type"></a> [chat\_instance\_type](#input\_chat\_instance\_type) | Scaleway commercial type for the dedicated StoatChat node. | `string` | `"DEV1-M"` | no |
| <a name="input_config_repo_branch"></a> [config\_repo\_branch](#input\_config\_repo\_branch) | Branch to track in the config repo | `string` | `"master"` | no |
| <a name="input_config_repo_path"></a> [config\_repo\_path](#input\_config\_repo\_path) | Path inside the config repo with Kubernetes manifests | `string` | `"terraform/k8s"` | no |
| <a name="input_config_repo_url"></a> [config\_repo\_url](#input\_config\_repo\_url) | Git repo URL containing Kubernetes manifests for this environment | `string` | n/a | yes |
| <a name="input_data_volume_snapshot_id"></a> [data\_volume\_snapshot\_id](#input\_data\_volume\_snapshot\_id) | Optional snapshot ID used by environments that should attach a copy of the prod data volume. | `string` | `""` | no |
| <a name="input_env_name"></a> [env\_name](#input\_env\_name) | Environment name | `string` | `"prod"` | no |
| <a name="input_github_bootstrap_terra_infra_webhook_pat"></a> [github\_bootstrap\_terra\_infra\_webhook\_pat](#input\_github\_bootstrap\_terra\_infra\_webhook\_pat) | Fine-grained PAT used only during bootstrap to create/patch GitHub webhook (not persisted) | `string` | `""` | no |
| <a name="input_github_persistent_terra_infra_ro_pat"></a> [github\_persistent\_terra\_infra\_ro\_pat](#input\_github\_persistent\_terra\_infra\_ro\_pat) | Fine-grained PAT (read-only) kept in cluster for repo access | `string` | n/a | yes |
| <a name="input_instance_image"></a> [instance\_image](#input\_instance\_image) | Scaleway instance image name or ID (e.g., ubuntu\_jammy, debian\_trixie). | `string` | `"debian_trixie"` | no |
| <a name="input_jenkins_data_volume_size_gb"></a> [jenkins\_data\_volume\_size\_gb](#input\_jenkins\_data\_volume\_size\_gb) | Size of the standalone Jenkins controller data volume in GiB. | `number` | `50` | no |
| <a name="input_jenkins_instance_type"></a> [jenkins\_instance\_type](#input\_jenkins\_instance\_type) | Scaleway commercial type for the standalone Jenkins controller node. | `string` | `"DEV1-S"` | no |
| <a name="input_luks_key_access_key"></a> [luks\_key\_access\_key](#input\_luks\_key\_access\_key) | Access key (read-only) for fetching LUKS key from Object Storage | `string` | `""` | no |
| <a name="input_luks_key_secret_key"></a> [luks\_key\_secret\_key](#input\_luks\_key\_secret\_key) | Secret key (read-only) for fetching LUKS key from Object Storage | `string` | `""` | no |
| <a name="input_luks_key_url"></a> [luks\_key\_url](#input\_luks\_key\_url) | Optional presigned URL to fetch LUKS key (overrides access/secret when set) | `string` | `""` | no |
| <a name="input_manual_snapshots"></a> [manual\_snapshots](#input\_manual\_snapshots) | Manual data-volume snapshots (keyed by name) that are kept until ttl\_hours expires (requires terraform apply to enforce). | <pre>map(object({<br/>    created_at = string<br/>    ttl_hours  = optional(number, 24)<br/>  }))</pre> | `{}` | no |
| <a name="input_observability_data_volume_size_gb"></a> [observability\_data\_volume\_size\_gb](#input\_observability\_data\_volume\_size\_gb) | Size of the dedicated observability data volume in GiB. | `number` | `10` | no |
| <a name="input_observability_instance_type"></a> [observability\_instance\_type](#input\_observability\_instance\_type) | Scaleway commercial type for the dedicated observability k3s node. | `string` | `"DEV1-M"` | no |
| <a name="input_owner_access_key"></a> [owner\_access\_key](#input\_owner\_access\_key) | Owner IAM access key used to resolve the Object Storage bucket policy principal. | `string` | `""` | no |
| <a name="input_prevent_destroy_data_volume"></a> [prevent\_destroy\_data\_volume](#input\_prevent\_destroy\_data\_volume) | Set false only when you intentionally want Terraform to allow destroying the data volume (e.g., wiping/recreating). | `bool` | `true` | no |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | Scaleway Project ID | `string` | n/a | yes |
| <a name="input_public_ip_address"></a> [public\_ip\_address](#input\_public\_ip\_address) | Existing Flexible IP address to attach (alternative to public\_ip\_id). | `string` | `""` | no |
| <a name="input_public_ip_id"></a> [public\_ip\_id](#input\_public\_ip\_id) | Existing Flexible IP ID to attach (leave empty to let Terraform create one). | `string` | `""` | no |
| <a name="input_sops_age_key"></a> [sops\_age\_key](#input\_sops\_age\_key) | Age private key used by Flux to decrypt SOPS-managed secrets (optional). | `string` | `""` | no |
| <a name="input_swap_file"></a> [swap\_file](#input\_swap\_file) | Swap file path used during bootstrap. | `string` | `"/swapfile"` | no |
| <a name="input_swap_size_gb"></a> [swap\_size\_gb](#input\_swap\_size\_gb) | Swap size in GiB for the node bootstrap. | `number` | `4` | no |
| <a name="input_swap_swappiness"></a> [swap\_swappiness](#input\_swap\_swappiness) | Kernel vm.swappiness value configured during bootstrap. | `number` | `10` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_chat_data_volume_id"></a> [chat\_data\_volume\_id](#output\_chat\_data\_volume\_id) | ID of the dedicated StoatChat data volume |
| <a name="output_chat_node_ip"></a> [chat\_node\_ip](#output\_chat\_node\_ip) | Public IP of the dedicated StoatChat node |
| <a name="output_data_volume_id"></a> [data\_volume\_id](#output\_data\_volume\_id) | ID of the environment's data volume |
| <a name="output_jenkins_data_volume_id"></a> [jenkins\_data\_volume\_id](#output\_jenkins\_data\_volume\_id) | ID of the standalone Jenkins controller data volume |
| <a name="output_jenkins_node_ip"></a> [jenkins\_node\_ip](#output\_jenkins\_node\_ip) | Public IP of the standalone Jenkins controller node |
| <a name="output_jenkins_store_publisher_access_key"></a> [jenkins\_store\_publisher\_access\_key](#output\_jenkins\_store\_publisher\_access\_key) | Access key for the Jenkins static store publisher |
| <a name="output_jenkins_store_publisher_secret_key"></a> [jenkins\_store\_publisher\_secret\_key](#output\_jenkins\_store\_publisher\_secret\_key) | Secret key for the Jenkins static store publisher |
| <a name="output_k3s_node_1_ip"></a> [k3s\_node\_1\_ip](#output\_k3s\_node\_1\_ip) | Public IP of k3s node |
| <a name="output_manual_snapshot_ids"></a> [manual\_snapshot\_ids](#output\_manual\_snapshot\_ids) | IDs of managed manual snapshots (empty when none are configured) |
| <a name="output_observability_data_volume_id"></a> [observability\_data\_volume\_id](#output\_observability\_data\_volume\_id) | ID of the dedicated observability data volume |
| <a name="output_observability_node_ip"></a> [observability\_node\_ip](#output\_observability\_node\_ip) | Public IP of the dedicated observability node |
| <a name="output_store_bucket_host"></a> [store\_bucket\_host](#output\_store\_bucket\_host) | Object Storage host used by the static store proxy |
| <a name="output_store_bucket_name"></a> [store\_bucket\_name](#output\_store\_bucket\_name) | Object Storage bucket used by the static store proxy |
<!-- END_TF_DOCS -->