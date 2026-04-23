# Proxmox Runner Layer

This document defines the neutral runner layer used by Jenkins jobs that need
temporary compute outside the Jenkins controller. It is not specific to DITT or
EX40. Those pipelines are consumers of this layer.

## Goals

- Keep Jenkins jobs capability-based. Jobs request labels, not Proxmox nodes or VM IDs.
- Keep Proxmox credentials and host details out of job definitions.
- Make runner allocation exclusive with a lease and a TTL.
- Always release leases, even when a job fails.
- Reset runners to a known state before execution.
- Keep the Jenkins controller free of build executors.

## Non-goals for the first pass

- No real Proxmox host names, VM IDs, IPs, or credentials are stored before read-only discovery.
- No mutable Jenkins UI-only configuration.
- No DITT-specific runner model.
- No runtime mutation of golden images.

## Concepts

Runner inventory is platform configuration. A runner has a stable ID, capability labels, and a backend-specific location.

Jobs only use labels:

```text
windows,gpu,nvidia,vulkan
```

The allocator maps those labels to a concrete runner:

```yaml
runners:
  - id: win-gpu-nvidia-01
    labels:
      - windows
      - gpu
      - nvidia
      - vulkan
    backend:
      type: proxmox
      node: example-node
      vmid: 100
    connection:
      type: winrm
    gpu:
      vendor: nvidia
      model: rtx-2070
    policy:
      lease_ttl_minutes: 120
      reset_before_use: true
      release_on_failure: true
```

The example inventory in `docs/proxmox-runner-inventory.example.yaml` is intentionally fake.

## Allocator Contract

The allocator should expose a small CLI or API with JSON-safe output and no secrets in stdout.

```bash
runnerctl lease --labels windows,gpu,nvidia,vulkan --ttl-minutes 120
runnerctl prepare --lease <lease-id>
runnerctl health --lease <lease-id>
runnerctl exec --lease <lease-id> -- <command>
runnerctl release --lease <lease-id>
```

The lease response should include only non-secret operational data:

```json
{
  "lease_id": "opaque-lease-id",
  "runner_id": "win-gpu-nvidia-01",
  "labels": ["windows", "gpu", "nvidia", "vulkan"],
  "connection": {
    "type": "winrm"
  }
}
```

Credentials remain in Jenkins credentials or SOPS-managed Kubernetes Secrets.

## State Model

Runner states:

- `available`: eligible for a new lease.
- `leased`: reserved by one job.
- `resetting`: reverting to a known snapshot or image state.
- `running`: executing a job.
- `failed`: failed health or cleanup and needs operator attention.
- `disabled`: intentionally removed from scheduling.

Every lease must have a TTL. A cleanup loop must be able to release stale leases or mark the runner failed without touching unrelated production infrastructure.

## Packer Role

Packer belongs below the runner layer. It should build reproducible base images or templates.

Initial design:

- Packer builds a generic Windows base template.
- GPU driver installation is handled as a profile or promotion step where practical.
- Runtime CI jobs do not mutate golden images.
- Jenkins jobs consume prepared runners. They do not build images inline.

Packer requires real Proxmox access, so the first implementation step after credentials is read-only discovery, then a minimal template build plan.

## Jenkins Integration

Current Jenkins job:

```text
ci/runners/proxmox-plan
```

This job validates the generic runner request contract and prints a dry-run execution plan. It does not talk to Proxmox.

Future jobs, including DITT or EX40 jobs, should depend on this runner layer through labels only. A job may request `windows,gpu,nvidia,vulkan`, but it must not hardcode Proxmox nodes, VM IDs, or storage names.

## First Proxmox Step

When credentials are available, the next safe step is read-only discovery:

1. list Proxmox nodes
2. list candidate VM templates and snapshots
3. list GPU-capable hosts
4. confirm API scopes
5. write the first real inventory through SOPS or a non-secret ConfigMap depending on sensitivity

Only after that should the first `lease -> reset -> health -> release` flow run against a real runner.
