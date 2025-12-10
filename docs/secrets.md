## Secrets (SOPS + age)

- Secrets stored as encrypted YAML (SOPS) in `terraform/k8s/vars/{prod,test}/secrets/`.
- Encryption uses age; public key in `.sops.yaml`, private key (`terra.agekey`) is local/1Password (never in repo).

### Set key in session
```
cd terraform
$env:SOPS_AGE_KEY = Get-Content terra.agekey -Raw
```

### Decrypt
```
sops -d k8s/vars/prod/secrets/kimai-admin-credentials.yaml
```
Extract a field:
```
sops -d --extract '["stringData"]["password"]' k8s/vars/prod/secrets/kimai-admin-credentials.yaml
```

### Create/update secret
1) Create plaintext YAML (stringData only), e.g.:
```
apiVersion: v1
kind: Secret
metadata:
  name: kimai-admin-credentials
  namespace: apps-tools
type: Opaque
stringData:
  username: admin
  email: admin@devsh.eu
  password: <generated>
```
2) Encrypt:
```
sops --encrypt --in-place k8s/vars/prod/secrets/kimai-admin-credentials.yaml
```
3) Commit encrypted file to the branch (`env/prod` or `env/test`).

### Generate strong password
```
python - <<'PY'
import secrets,string
alpha=string.ascii_letters+string.digits
print(''.join(secrets.choice(alpha) for _ in range(32)))
PY
```

### Flux decryption
- Flux uses secret `sops-age` in `flux-system` (private age key) and `spec.decryption.provider: sops`.
- Only `data/stringData` are encrypted (see `.sops.yaml`).
