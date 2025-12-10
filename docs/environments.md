## Environments & Branches

- `env/prod` branch → production cluster (workspace `prod`)
- `env/test` branch → test cluster (workspace `test`)
- Flux in each cluster watches its branch; manifests are the same structure.
- Workflow: commit to `env/test` → validate in test → PR/merge to `env/prod` for production.

### Terraform workspace mapping
- Prod: `terraform workspace select prod`, `.env` uses `TF_VAR_env_name=prod`, `TF_VAR_config_repo_branch=env/prod`.
- Test: `terraform workspace select test`, set env vars per session:
  ```
  $env:TF_VAR_env_name='test'
  $env:TF_VAR_config_repo_branch='env/test'
  # optional: TF_VAR_data_volume_snapshot_id to restore prod snapshot
  ```

### DNS
- Manual for now: point prod hostnames to prod node IP; test hostnames to test node IP.
- Hosts:
  - prod: `www.devsh.eu`, `blog.devsh.eu`, `kimai2.devsh.eu`, `monitoring.devsh.eu`, `flux-hook.devsh.eu`
  - test: `test.www.devsh.eu`, `test.blog.devsh.eu`, etc. (update A records to test IP)
