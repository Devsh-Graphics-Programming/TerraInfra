# DITT Path Tracer CI

This runbook covers the current EX40 DITT validation flow. The runner platform
is generic and documented in `docs/proxmox-runners.md`; this file only describes
how the DITT jobs consume it.

## Flow

1. GitHub Actions builds the Release EX40 runtime package on `ptCLI`.
2. The `Run Path Tracer Jenkins` workflow sends only the small runtime package
   to Jenkins.
3. Jenkins starts `ci/ditt/real/ex40-public` and `ci/ditt/real/ex40-private`.
4. Each Jenkins job leases a Windows GPU runner by labels through `withRunner`.
5. Scene and reference data are materialized on the runner from the runner-farm
   Git object cache, not uploaded through Jenkins.
6. Reports are written on the runner and compressed into a transient
   `publish.zip`.
7. Jenkins streams that zip to the generic `runnerctl` store publish endpoint.
8. `runnerctl` publishes to the approved latest prefix, writes
   `publish-manifest.json`, and prunes stale objects not present in the current
   report.

## Reports

Current latest endpoints:

- Public full suite: `https://store.devsh.eu/ditt/public/latest/`
- Private full suite: `https://store.devsh.eu/ditt/private/latest/`
- Public compare smoke: `https://store.devsh.eu/ditt/public/smoke/latest/`

The public full-suite job runs the tiny three-input compare smoke before the
expensive render. This keeps report-set and compare UI behavior covered without
running a second full render mode.

## Result Semantics

- `SUCCESS`: infrastructure, runtime, publish, and report validation succeeded.
- `UNSTABLE`: the EX40 report was produced and published, but the report
  contains failed comparisons.
- `FAILURE`: infrastructure, runtime startup, scene materialization, report
  generation, or publish failed.

GitHub treats Jenkins `SUCCESS` and `UNSTABLE` as a successful workflow result,
because report failures are data and should remain visible in the report rather
than hiding the generated payload behind a failed workflow.

## Storage Rules

- Jenkins archives only small diagnostics such as logs, summary JSON files,
  source metadata, and `run-summary.json`.
- Jenkins must not archive `publish.zip` or full report bundles.
- Object Storage keeps the latest report prefixes. `publish-manifest.json` plus
  prune removes stale objects under approved prefixes after a successful
  publish.
- Store publishing credentials stay in the controller-side `runnerctl`
  environment. Windows runners do not receive Object Storage keys.

## Local Report Check

Use the committed report viewer server from the EX40 example. Do not add a
second static server.

```bat
cd examples_tests\40_PathTracer
python report\server.py --no-browser
```

Open a payload by changing the fragment:

```text
http://127.0.0.1:8040/report/#/bin/out/<payload>
```

Before changing report UI files, validate at least:

- a normal render report payload
- the report-set index payload
- one compare pair payload such as `#/bin/out/<payload>/pairs/amd_vs_nvidia`
- EXR preview open/close and direct EXR links

## Operational Checklist

- Keep jobs capability-based: request labels, not Proxmox nodes, VMIDs, storage
  names, or PCI IDs.
- Keep workload behavior in Jenkins shared-library code or EX40. Do not add DITT
  semantics to `runnerctl`.
- Keep source data transport small. Jenkins receives the runtime package only;
  scenes and references come from runner-local caches.
- Keep full-scene timeouts generous. Private jobs intentionally use per-scene
  isolation so slow scenes become report data instead of aborting the whole
  infrastructure job.
- If a newer GitHub run supersedes an older one, Actions cancels the old run and
  Jenkins jobs use `abortPrevious=true` for the matching job.
