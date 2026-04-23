# Packer

This directory is reserved for the Proxmox image pipeline used by the Jenkins
runner platform.

Target layout:

- `proxmox/windows-base/`
- `proxmox/windows-gpu-nvidia/`
- `proxmox/common/`

Planned image flow:

1. build `windows-base` with the `proxmox-iso` builder
2. build `windows-gpu-nvidia` with the `proxmox-clone` builder
3. run image-level smoke checks
4. promote the resulting template to a stable channel

Design rules:

- Packer is the only supported image build path.
- Runtime jobs never mutate golden templates.
- The runtime path uses linked clones and destroy-after-job behavior.
- Proxmox API credentials and sensitive host details stay outside committed
  templates and are injected through local vars or SOPS-managed secrets.

See `docs/proxmox-runners.md` for the platform contract and lifecycle.
