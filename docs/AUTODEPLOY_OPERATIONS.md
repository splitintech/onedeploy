# AutoDeploy operations and recovery

This is a single-host library integration preview, not an unattended production service.

## Durable controller

Keep SQLite on persistent controller-local storage, not an ephemeral GPU sandbox or FUSE volume. One controller host owns it; cross-process advisory locking protects local operations and OS releases the lock on process exit. Multi-host shared-filesystem coordination is unsupported. Back up the database using SQLite's backup mechanism at a consistent point, retain it securely and test restore. It contains source/release metadata, not provider credentials.

A monotonic generation prevents an older plan from promoting after a newer release has superseded it. Plans use source/configuration digests, releases use deterministic identities, promotion uses compare-and-swap, and state transitions append an event journal.

## Unknown outcomes

The engine writes operation intent before a provider mutation. A timeout or crash becomes `needs_reconciliation`; never blindly replay an unknown write. `inspect_operation` must provide read-only evidence that the exact operation completed. If evidence is unavailable, stop and inspect the provider. An exception is not evidence that nothing happened. Exceptions are not persisted verbatim because provider text may contain credentials.

Provider-owned resources are tied to account, deployment and release identity. Never delete resources on a guessed name, different account or incomplete ownership evidence. Retain shared model volumes and prior snapshots; worker deletion is not release-artifact garbage collection.

## Promotion and draining

The integrating router must read the authoritative desired release before assigning new work. It must stop admissions to old workers, preserve in-flight job leases and make completion/idempotency authoritative. The engine changes desired release before draining the previous worker; without this routing integration, using the library does not redirect traffic.

Candidate failure preserves the previous desired release. Failed or superseded candidates are visible in state and can be cleaned through `engine.cleanup(release_id, approved=True)`. Cleanup refuses the currently desired worker. Use owner-checked drain/delete operations; never blindly kill an active request. A newer plan can supersede a partially completed rollout, so inspect and clean older leftover candidates rather than assuming they were removed.

Rollback uses a previously qualified release of the same deployment, creates a new generation/candidate, qualifies it again and changes desired release before draining. It does not reverse database migrations or delete shared persistent data. Preserve enough candidate overlap within the worker cap; if capacity or budget is unavailable, leave old healthy traffic running.

## Costs and lifecycle

The library requires explicit resource settings and exposes reviewed budget/worker limits. These values alone do not implement a billing meter. The live adapter requires a trusted policy callback to authorize operations against current quota, costs, concurrent workers and TTL limits. Do not substitute an always-true callback in production.

Actual on-demand autoscaling, job admission, cost telemetry and periodic orphan cleanup need a persistent host integration. GPU sandboxes are ephemeral on stop; no paused VRAM is promised. Idle settings do not guarantee safety for in-flight application work unless the controller renews activity/leases correctly. Model weights can be shared as immutable checked artifacts, but each GPU loads its own model into memory.

## Production gates

Before paid rollout: verify 0.231.0 against the current provider, registry access, selected GPU, account quota and spending limit; qualify authenticated worker protocol, source provenance, CUDA/model placement, synthetic output, cold starts, failover, drainage, rollback and deletion. Before replacing CPU Whisper: complete a limited rollout and keep the Railway voice-prompt API/orchestration. Do not remove the whole service.
