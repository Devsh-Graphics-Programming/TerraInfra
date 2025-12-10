## DNS

Manual for now:
- Prod A records → prod node IP (current): `51.158.114.92`
- Test A records → test node IP (current): `212.47.251.150`

Hosts:
- Prod: `www.devsh.eu`, `blog.devsh.eu`, `kimai2.devsh.eu`, `monitoring.devsh.eu`, `flux-hook.devsh.eu`
- Test: prefixed equivalents (`test.www.devsh.eu`, etc.) — update to current test IP

Certs:
- Let’s Encrypt HTTP-01 via cert-manager. Ensure DNS points correctly; allow a few minutes for propagation. Kimai cert is subject to LE rate limits if hammered; retry after window if needed.

Future: automate DNS updates via API (not yet wired).
