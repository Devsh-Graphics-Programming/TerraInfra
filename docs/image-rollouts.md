# Image Rollouts

Container services that publish mutable channel tags use this production model:

1. The application repository builds and pushes an immutable audit tag plus a stable channel tag such as `latest`.
2. Kubernetes manifests point at the stable channel tag and set `imagePullPolicy: Always`.
3. The cluster exposes a Flux `Receiver` for image webhooks, backed by an HMAC secret. The repository that publishes the image sends GitHub package webhooks to that receiver so Flux image-reflector can rescan immediately.
4. A small digest rollout job compares the live pod image digest with the registry digest for the stable tag. If the digest changed, it patches a deployment pod-template annotation and lets Kubernetes perform a normal rollout.
5. The digest rollout job also runs on a short schedule as fallback in case a webhook delivery is delayed or missed.

Do not use `ImageUpdateAutomation` for normal production image rollout. It requires cluster write access back to TerraInfra and creates per-image tag bump commits. Immutable tags are still useful for audit, rollback, and debugging, but deployment manifests should not need a commit for every image publication.

For a new image-backed service, add:

- an `ImageRepository` for observability and webhook-triggered rescans,
- a `Receiver` on the service cluster,
- a package webhook from the image-producing GitHub repository,
- a service-specific digest rollout CronJob or reusable rollout hook,
- a stable channel tag in the workload manifest,
- `imagePullPolicy: Always`.

The webhook is the fast path. The CronJob is only the safety net.
