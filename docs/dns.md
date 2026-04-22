## DNS

Manual for now (TODO: automate via DNS API):
- Prod A records -> prod node IP (use current IP from Terraform outputs)
- Test A records -> test node IP (use current IP from Terraform outputs)

Hosts:
- Prod app node: `devsh.eu`, `www.devsh.eu`, `blog.devsh.eu`, `kimai2.devsh.eu`, `store.devsh.eu`, `flux-hook.devsh.eu`, `k8s.devsh.eu`
- Prod observability node: `monitoring.devsh.eu`, `oncall.devsh.eu`
- Prod chat node: `stoatchat.devsh.eu`
- Prod CI node: `jenkins.devsh.eu`
- Test: prefixed equivalents (`test.www.devsh.eu`, etc.) - update to current test IP

Certs:
- Let's Encrypt HTTP-01 via cert-manager. Ensure DNS points correctly; allow a few minutes for propagation. Kimai cert is subject to LE rate limits if hammered; retry after window if needed. After DNS change you can force re-issue per cert: `k3s kubectl -n <ns> delete order,challenge -l acme.cert-manager.io/certificate-name=<cert_name>`.

Future: automate DNS updates via API (not yet wired).
