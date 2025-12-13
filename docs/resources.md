# Resource management (CPU/RAM) and uptime

This cluster is a single-node k3s setup, so one noisy workload can starve everything else. The goal is to keep user-facing services responsive (www/blog, Kimai) and make batch jobs “yield” under pressure.

## What we enforce
- Requests/limits on key workloads to prevent unbounded RAM usage.
- Priority classes so critical apps can preempt low-priority batch pods when the node is under pressure.
- Rollout strategies that avoid briefly running duplicate heavy pods on a single node.

## Where it is defined
- Priority classes: `terraform/k8s/priority-classes.tpl.yaml`
  - `prod-app-critical` (user-facing apps)
  - `prod-batch-low` (non-critical batch jobs)
- Website/Blog deployments: `terraform/k8s/www-sites.tpl.yaml`
- Kimai + MariaDB deployments: `terraform/k8s/kimai.tpl.yaml`
- Batch CronJobs (example): `terraform/k8s/image-digest-rollout.yaml`

## How to check CPU/RAM usage
Run on the node:
```
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
```

Kubernetes view (requires metrics-server):
```
k3s kubectl top nodes
k3s kubectl top pods -A --sort-by=memory
```

Node view:
```
free -h
ps aux --sort=-rss | head -n 20
```

Recent evictions / OOM signals:
```
k3s kubectl get events -A --sort-by=.lastTimestamp | tail -n 80
dmesg -T | rg -i "oom|killed process" || true
```

## How to change resources safely
- Keep limits realistic; on a single node, “too high” limits just move OOM pressure to the node.
- Use `priorityClassName: prod-app-critical` only for user-facing services that must stay up.
- For single replicas of stateful workloads, avoid rollouts that create duplicates. Prefer `maxSurge: 0` (or `Recreate`) and a sensible `terminationGracePeriodSeconds`.

