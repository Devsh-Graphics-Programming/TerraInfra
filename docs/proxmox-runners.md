# Proxmox Runner Platform

This document defines the neutral runner platform used by Jenkins jobs that need
temporary compute outside the Jenkins controller. It is not specific to DITT or
EX40. Those pipelines are consumers of this platform.

## Goals

- Keep Jenkins jobs capability-based. Jobs request labels, not Proxmox nodes,
  VM IDs, or storage names.
- Keep Proxmox credentials and host details out of job definitions.
- Make runner allocation exclusive with a lease and a TTL.
- Always destroy runtime clones after a job, even when the job fails.
- Keep runtime jobs fast by building images out of band and preferring warm
  clones over cold provisioning.
- Keep the Jenkins controller free of build executors.

## Non-goals

- No pet VM workflow.
- No runtime mutation of golden images.
- No Jenkins UI-only configuration.
- No DITT-specific or EX40-specific runner naming.
- No direct use of `DummyOS` or manual restore snapshots as the durable model.

## Design Principles

1. Packer is the only supported way to build or promote runner images.
2. Runtime jobs consume promoted templates and disposable linked clones.
3. The hot path should prefer warm clones. Image build and heavy provisioning
   stay out of the job path.
4. The allocator owns lease state, warm pool refill, and janitor safety checks.
5. Jenkins asks for capabilities only. The allocator maps those capabilities to
   a concrete runner class and backend placement.

## Platform Components

### 1. Packer image pipeline

Packer belongs below the runner layer. It builds reproducible Proxmox templates.

Recommended image families:

- `windows-base`
- `windows-gpu-nvidia`

Recommended build flow:

- `windows-base` is built with the `proxmox-iso` builder from a Windows ISO.
- `windows-gpu-nvidia` is built with the `proxmox-clone` builder from the
  promoted `windows-base` template.
- GPU driver installation happens during image promotion, not during job
  runtime.
- Every promoted template gets a channel such as `windows-base/stable` or
  `windows-gpu-nvidia/stable`.

### 2. Runner classes

Jobs only use labels:

```text
windows,gpu,nvidia,vulkan,gpu-class-rtx-2070
```

The allocator resolves those labels to a runner class. A runner class points to
a template channel and to Proxmox placement policy.

### 3. Allocator / runnerctl

The allocator should expose a small CLI or API with JSON-safe output and no
secrets in stdout.

```bash
runnerctl lease --class win-gpu-nvidia --labels windows,gpu,nvidia,vulkan --ttl-minutes 120
runnerctl prepare --lease <lease-id>
runnerctl health --lease <lease-id>
runnerctl release --lease <lease-id>
```

The lease response should include only non-secret operational data:

```json
{
  "lease_id": "opaque-lease-id",
  "runner_class": "win-gpu-nvidia",
  "labels": ["windows", "gpu", "nvidia", "vulkan", "gpu-class-rtx-2070"],
  "connection": {
    "type": "winrm"
  }
}
```

Credentials remain in Jenkins credentials or SOPS-managed Kubernetes Secrets.

### 4. Warm pool

Fast job startup requires a warm pool:

- the allocator keeps a small number of ready stopped clones per runner class
- jobs prefer a warm clone over a cold clone
- a background reconciler refills the pool after lease release
- the pool is bounded per class and per host

With a single physical RTX 2070, the first practical target is:

- `min_ready = 1`
- `max_ready = 1`
- `gpu_exclusive = true`

### 5. Janitor

The janitor must be hard-scoped:

- only the dedicated Proxmox pool
- only the configured VMID range
- only resources tagged by the runner platform
- only stale leases older than the configured TTL window

It must never touch unrelated production infrastructure.

## Access Model

The runtime backend should use a Proxmox API token with the smallest practical
scope for the dedicated runner pool, VMID range, and storage targets.

Recommended split:

- Proxmox API token for allocator automation
- WinRM for guest execution and health checks
- root SSH only for operator/debug tasks

Secrets should be delivered through SOPS-managed Kubernetes Secrets or Jenkins
credentials. They must not be committed into job definitions, docs examples, or
workflow inputs.

## State Model

Runner class states:

- `ready`: warm clone is available for immediate lease
- `leased`: reserved by one job
- `creating`: clone is being created from a template
- `booting`: guest is starting
- `healthy`: guest passed health checks and is ready for workload
- `draining`: temporarily removed from scheduling
- `failed`: operator attention is needed

Every lease must have a TTL. A cleanup loop must be able to release stale
leases or destroy stale clones without touching unrelated infrastructure.

## Runtime Lifecycle

### Hot path

The preferred fast path is:

```text
lease -> acquire warm clone -> boot -> health -> execute -> destroy
```

### Cold path

When no warm clone is available:

```text
lease -> resolve template -> linked clone -> boot -> health -> execute -> destroy
```

Cold path should be the exception, not the normal case.

## Inventory Model

Platform configuration should be committed as code. Real host details or
sensitive mappings can later move to SOPS-managed config, but the public example
should already reflect the final data model.

The example inventory in `docs/proxmox-runner-inventory.example.yaml` is
intentionally fake and describes:

- template channels
- runner classes
- warm pool policy
- janitor scope

The live Jenkins controller also receives:

- a SOPS-managed Kubernetes Secret with the Proxmox API URL and API token
- a committed inventory ConfigMap mounted on the controller for future allocator
  and image tooling work

## Example Platform Layout

```yaml
version: 2
templates:
  - id: windows-base-stable
    channel: windows-base/stable
    builder: proxmox-iso
  - id: windows-gpu-nvidia-stable
    channel: windows-gpu-nvidia/stable
    builder: proxmox-clone
    parent: windows-base-stable
runner_classes:
  - id: win-gpu-nvidia
    labels:
      - windows
      - gpu
      - nvidia
      - vulkan
      - gpu-class-rtx-2070
    template: windows-gpu-nvidia-stable
```

## Health Checks

Health checks should verify runtime readiness, not just file presence.

Recommended minimum checks for a Windows GPU runner:

- guest agent responds
- WinRM responds
- NVIDIA device is visible
- Vulkan runtime is usable
- workspace path is ready

The allocator should record which checks passed and expose that to Jenkins in a
sanitized form.

## Storage Strategy

The runner platform should assume that storage pressure exists and optimize for
thin, local, disposable runtime state:

- linked clones instead of full clones
- local fast storage for active runtime clones
- image promotion out of band
- no NAS restore flow in the hot path

## Observability

The platform should export enough data to explain both correctness and speed:

- lease latency
- warm-pool hit rate
- cold-clone fallback count
- clone creation time
- boot time
- health check time
- janitor cleanup count
- stale lease count

## Jenkins Integration

Current Jenkins jobs:

- `ci/runners/proxmox-plan`
- `ci/runners/packer-plan`
- `ci/runners/proxmox-api-smoke`
- `ci/runners/proxmox-warm-smoke`

The plan jobs stay in dry-run mode until the real allocator and Packer execution
paths are connected.

The smoke jobs already use the live Proxmox API token and verify:

- read-only API reachability and pool/storage visibility
- scratch template creation
- linked clone creation
- start/stop lifecycle
- destroy and cleanup behavior

Future jobs, including DITT or EX40 jobs, should depend on this platform only
through labels and runner classes. They must not hardcode Proxmox nodes, VM
IDs, storage names, or mutable template names.

## First Real Implementation Order

1. commit the platform contract and validation rules
2. wire real inventory/config delivery through GitOps and SOPS
3. create the Packer template build and promotion flow
4. implement allocator lease/create/health/destroy logic
5. add warm pool reconciliation
6. run the first `lease -> clone -> health -> destroy` lifecycle test
7. connect consumer jobs such as DITT and EX40
