# Proxmox Runner Platform

This document defines the neutral runner platform used by Jenkins jobs that need
temporary compute outside the Jenkins controller. It is not specific to DITT or
EX40. Those pipelines are consumers of this platform.

## Goals

- Keep Jenkins jobs capability-based. Jobs request labels, not Proxmox nodes,
  VM IDs, storage names, PCI IDs, or tunnel ports.
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
- GPU driver and runtime-only components happen during image promotion, not
  during job runtime.
- Runtime images intentionally avoid developer stacks. They should contain only
  workload prerequisites such as GPU driver, VC++ redistributables, Vulkan
  runtime, Java for Jenkins remoting, workspace directories, and guest
  management services.
- Every promoted template gets a channel such as `windows-base/stable` or
  `windows-gpu-nvidia/stable`.

### 2. Runner classes

Jobs only use labels:

```text
windows,gpu,nvidia,vulkan,runtime-only,gpu-class-rtx-2070
```

The allocator resolves those labels to a runner class. A runner class points to
a template channel and to Proxmox placement policy.

### 3. Allocator / runnerctl

The allocator exposes a small local API with JSON-safe output and no secrets in
stdout. Jenkins pipelines should use a minimal helper and then switch to normal
Jenkins syntax on the leased runner:

```groovy
runner = runnerLease(labels: ['windows', 'gpu', 'nvidia', 'vulkan', 'runtime-only'])

node(runner.label) {
  powershell 'nvidia-smi'
}

runnerRelease(runner)
```

The controller provides those helpers through the `devsh-ci` shared library.
Normal jobs should not copy allocator HTTP code. For jobs that only need a
leased runner around native Jenkins stages, prefer:

```groovy
withRunner(labels: ['windows', 'gpu', 'nvidia', 'vulkan', 'runtime-only']) { runner ->
  stage('GPU sanity') {
    powershell 'nvidia-smi'
  }
}
```

`withRunner` logs the client-side wall time for the lease request, entry into
the Jenkins node, and release. When `runnerctl` returns backend timings, the
helper prints the allocator phases as sanitized operational data. Hot-pool jobs
also print the separate pool-build provenance timings so current lease latency
does not get mixed with the out-of-band VM refill cost.

Consumer jobs can add a readiness budget without changing the allocator
contract:

```groovy
withRunner(
  labels: ['windows', 'gpu', 'nvidia', 'vulkan', 'runtime-only'],
  maxReadySeconds: 10
) {
  powershell 'nvidia-smi'
}
```

The lease response should include only non-secret operational data:

```json
{
  "lease_id": "opaque-lease-id",
  "runner_class": "win-gpu-nvidia",
  "labels": ["windows", "gpu", "nvidia", "vulkan", "runtime-only", "gpu-class-rtx-2070"],
  "label": "runner-lease-opaque",
  "node_name": "runner-lease-opaque",
  "agent_online": true
}
```

Credentials remain in Jenkins credentials or SOPS-managed Kubernetes Secrets.

### 4. Warm pool

Fast job startup requires a warm pool:

- the allocator keeps a small number of already booted and health-checked
  disposable clones per runner class
- jobs prefer a hot pool lease over a cold clone
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
- interrupted hot-pool members stuck in an in-progress state are treated as
  stale after a short dedicated pool-member window

It must never touch unrelated production infrastructure.

## Access Model

The runtime backend should use a Proxmox API token with the smallest practical
scope for the dedicated runner pool, VMID range, and storage targets.

Recommended split:

- Proxmox API token for allocator automation
- WinRM for guest execution and health checks
- root SSH only for operator/debug tasks
- reverse SSH tunnel only when the Proxmox host is private-only and there is no
  direct Jenkins route into that network yet
- an isolated runner VLAN can expose only a host-local Jenkins HTTPS forward; in
  that case `network.jenkins_host_alias_ip` tells `runnerctl` which address to
  add to the guest hosts file for the Jenkins public hostname

Secrets should be delivered through SOPS-managed Kubernetes Secrets or Jenkins
credentials. They must not be committed into job definitions, docs examples, or
workflow inputs.

## State Model

Runner class states:

- `ready`: hot clone is already booted, health-checked, and available for
  immediate lease
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
lease -> acquire ready hot clone -> cached health guard -> execute -> destroy
```

The hot clone is still disposable. It is destroyed after the job and replaced
by the background reconciler, so the platform does not rely on mutable pet VMs
or runtime snapshot rollback.

Hot pool members are fully health-checked before they enter the `ready` state.
When a job leases a fresh hot member, the allocator can reuse that recent
health result and only perform a live guest-agent guard before handing the VM
to the job. If the cached result is too old, the allocator falls back to full
live health checks.

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

- Proxmox hosts and their capabilities
- template channels
- template placements per host
- runner classes
- reverse tunnel API endpoints per host
- warm pool policy
- janitor scope

The live Jenkins controller also receives:

- a SOPS-managed Kubernetes Secret with Proxmox API token material
- a committed inventory ConfigMap mounted on the controller for allocator and
  image tooling work
- a `runnerctl` sidecar in the Jenkins pod that consumes the Proxmox secret and
  exposes a local HTTP API on `127.0.0.1:18080` for controller jobs
- a Flux-managed reverse tunnel SSH endpoint in the Jenkins pod so private-only
  Proxmox hosts can expose `127.0.0.1:<port>` API access without opening the
  Proxmox API publicly

## Example Platform Layout

```yaml
version: 3
proxmox:
  hosts:
    - id: example-rtx-node
      node: example-pve-node
      labels:
        - windows
        - gpu
        - nvidia
        - vulkan
        - runtime-only
        - gpu-class-rtx-2070
      api:
        url: https://127.0.0.1:18006/api2/json
        tunnel:
          type: reverse-ssh
          remote_bind_port: 18006
      pools:
        templates: example-ci-images
        runners: example-ci-runners
      storage:
        runtime: example-fast-lvm
      vmid_ranges:
        runner:
          start: 2000
          end: 2099
templates:
  - id: windows-base-stable
    channel: windows-base/stable
    builder: proxmox-iso
    placements:
      - host: example-rtx-node
        vmid: 9001
  - id: windows-gpu-nvidia-stable
    channel: windows-gpu-nvidia/stable
    builder: proxmox-clone
    parent: windows-base-stable
    placements:
      - host: example-rtx-node
        vmid: 9002
runner_classes:
  - id: win-gpu-nvidia
    labels:
      - windows
      - gpu
      - nvidia
      - vulkan
      - runtime-only
      - gpu-class-rtx-2070
    template: windows-gpu-nvidia-stable
    host_selector:
      labels:
        - windows
        - gpu
        - nvidia
        - vulkan
        - gpu-class-rtx-2070
    runtime:
      pool: runners
      storage: runtime
      vmid_range: runner
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

`runnerctl` exposes this through sanitized response fields named `timings` and
through the pool status endpoint. The values are durations and runner state
only. They do not include credentials, Proxmox token material, guest command
secrets, or raw provider response bodies.

Janitor and pool refill responses also include sanitized timings. This is used
by smoke jobs to show whether time was spent scanning lease state, checking VM
metadata, deleting Jenkins nodes, destroying VMs, or building a replacement hot
pool member.

## Jenkins Integration

Current Jenkins jobs:

- `ci/runners/proxmox-plan`
- `ci/runners/packer-plan`
- `ci/runners/smoke/proxmox-api`
- `ci/runners/smoke/proxmox-warm-clone`
- `ci/runners/smoke/proxmox-pool-ready`
- `ci/runners/smoke/proxmox-consumer-preflight`
- `ci/runners/smoke/proxmox-hot-pool-lifecycle`
- `ci/runners/smoke/proxmox-runtime-lifecycle`
- `ci/runners/status/proxmox-pool-status`
- `ci/runners/examples/windows-gpu-hello`

Current farm access model:

- each private Proxmox host opens a reverse SSH tunnel into the Jenkins pod
- the Jenkins pod exposes a restricted SSH endpoint on port `30222`
- each tunnel binds that host's Proxmox API to a configured local loopback port
  inside the Jenkins pod
- the `runnerctl` sidecar reads those local loopback API URLs from inventory and
  token material from Kubernetes Secret env vars
- runner VMs on the isolated VLAN reach Jenkins through the per-host
  `network.jenkins_host_alias_ip` address, keeping the job syntax independent of
  Proxmox networking details
- Jenkins jobs call only the local `runnerctl` HTTP API on `127.0.0.1:18080`
- consumer jobs lease a temporary Jenkins node by labels, then run normal
  declarative or scripted Pipeline steps on `node(runner.label)`
- future Proxmox hosts scale by adding another host entry, restricted key, and
  local reverse tunnel port while keeping the Jenkins job contract unchanged

The plan jobs stay capability-oriented and do not accept Proxmox nodes, storage
names, VMIDs, PCI IDs, or mutable template names as job parameters.

The smoke jobs already use the live Proxmox API token and verify:

- read-only API reachability and pool/storage visibility
- scratch template creation
- linked clone creation
- start/stop lifecycle
- safe janitor cleanup for stale tracked runner leases
- hot-pool refill and readiness before consumer jobs
- consumer-facing label lease, Windows runtime probe, GPU sanity, release, and
  wall-time readiness budget
- real template lease, boot, QEMU Guest Agent health, and release
- destroy and cleanup behavior

Future jobs, including DITT or EX40 jobs, should depend on this platform only
through labels and runner classes. They must not hardcode Proxmox nodes, VM
IDs, storage names, or mutable template names.

## Host-side tunnel assets

The durable host-side tunnel install assets live in the repo:

- `scripts/proxmox-runners/install-proxmox-runner-tunnel.sh`
- `scripts/proxmox-runners/proxmox-runner-tunnel.env.example`
- `scripts/proxmox-runners/install-proxmox-runner-artifact-server.sh`
- `scripts/proxmox-runners/proxmox-runner-artifact-server.env.example`

These assets are intended for Proxmox hosts that stay outside k3s/Flux but must
still follow the same repo-driven operational contract. The installer expects
the SSH key and `known_hosts` file to be provisioned locally and writes a
systemd unit plus an env file without committing secrets.

The artifact server is for large non-committed runtime installers such as GPU
drivers and redistributables. Packer should pull those from a host-local URL
instead of uploading large installers through WinRM.

## First Real Implementation Order

1. commit the platform contract and validation rules
2. wire real inventory/config delivery through GitOps and SOPS
3. create the Packer template build and promotion flow
4. implement allocator lease/create/health/destroy logic
5. add warm pool reconciliation
6. run the first `lease -> clone -> health -> destroy` lifecycle test
7. connect consumer jobs such as DITT and EX40
