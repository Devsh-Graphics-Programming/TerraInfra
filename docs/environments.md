## Environments & Branches

- Branch -> cluster: `env/prod` -> prod, `env/test` -> test.
- Workflow: commit to `env/test` -> validate in test -> fast-forward `env/prod` (see `docs/how-to-commit.md`).
- WARNING: push to `env/prod` reconciles live prod; push to `env/test` reconciles test.

### Terraform workspace mapping
- Prod: `terraform workspace select prod`, `.env` uses `TF_VAR_env_name=prod`, `TF_VAR_config_repo_branch=env/prod`.
- Test: `terraform workspace select test`, set per session:
  ```
  $env:TF_VAR_env_name='test'
  $env:TF_VAR_config_repo_branch='env/test'
  # optional: TF_VAR_data_volume_snapshot_id to restore prod snapshot
  ```

### DNS (manual)
- Point prod hosts to prod IP; test hosts to test IP. See `docs/dns.md` for current hosts and notes.
