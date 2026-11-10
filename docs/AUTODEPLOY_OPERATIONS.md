# AutoDeploy operations and recovery

This is a single-host controller/library engineering preview. The runtime is implemented, but no production service is installed or live-qualified by this repository.

## Durable controller

Keep SQLite on persistent controller-local storage, not an ephemeral GPU sandbox or FUSE volume. One controller host owns it; cross-process advisory locking protects local operations and OS releases the lock on process exit. Multi-host shared-filesystem coordination is unsupported. Back up the database using SQLite's backup mechanism at a consistent point, retain it securely and test restore. It contains source/release metadata, not provider credentials.

A monotonic generation prevents an older plan from promoting after a newer release has superseded it. Plans use source/configuration digests, releases use deterministic identities, promotion uses compare-and-swap, and state transitions append an event journal.

## Unknown outcomes

The engine writes operation intent before a provider mutation. A timeout or crash becomes `needs_reconciliation`; never blindly replay an unknown write. `inspect_operation` must provide read-only evidence that the exact operation completed. If evidence is unavailable, stop and inspect the provider. An exception is not evidence that nothing happened. Exceptions are not persisted verbatim because provider text may contain credentials.

Provider-owned resources are tied to account, deployment and release identity. Never delete resources on a guessed name, different account or incomplete ownership evidence. Retain shared model volumes and prior snapshots; worker deletion is not release-artifact garbage collection.

## Promotion and draining

The implemented controller reads the authoritative desired release before assigning new work, holds durable in-flight job leases and fences late results. An external application router must forward to this stable authenticated controller rather than sandbox URLs. The engine changes desired release before draining the previous worker; without this routing integration, using the library does not redirect traffic.

Candidate failure preserves the previous desired release. Failed or superseded candidates are visible in state and can be cleaned through `engine.cleanup(release_id, approved=True)`. Cleanup refuses the currently desired worker. Use owner-checked drain/delete operations; never blindly kill an active request. A newer plan can supersede a partially completed rollout, so inspect and clean older leftover candidates rather than assuming they were removed.

Rollback uses a previously qualified release of the same deployment, creates a new generation/candidate, qualifies it again and changes desired release before draining. It does not reverse database migrations or delete shared persistent data. Preserve enough candidate overlap within the worker cap; if capacity or budget is unavailable, leave old healthy traffic running.

## Costs and lifecycle

The library requires explicit resource settings and exposes reviewed budget/worker limits. These values alone do not implement a billing meter. The CLI supplies a durable budget ledger bound to exact configuration/runtime policy. It reserves full lifetime plus overhead per allocation, never automatically refunds uncertain spend, and retires active capacity only after verified deletion. Do not substitute an always-true callback in a custom integration. Rates are operator estimates and must be current.

The implemented controller performs bounded on-demand pooling, job admission, candidate adoption and idle cleanup while it is running. It must be supervised on a persistent host. Actual billing telemetry and provider alerts remain separately configured. GPU sandboxes are ephemeral on stop; no paused VRAM is promised. Idle settings do not guarantee safety for in-flight application work unless the controller renews activity/leases correctly. Model weights can be shared as immutable checked artifacts, but each GPU loads its own model into memory.

## Production gates

Before paid rollout: verify 0.231.0 against the current provider, registry access, selected GPU, account quota and spending limit; qualify authenticated worker protocol, source provenance, CUDA/model placement, synthetic output, cold starts, failover, drainage, rollback and deletion. Before replacing CPU Whisper: complete a limited rollout and keep the Railway voice-prompt API/orchestration. Do not remove the whole service.


## Serving and privacy

The CLI serves only loopback and requires a separate controller bearer token. Remote Railway integration requires an explicitly configured TLS/private-network front door; the program does not change firewall/VPN settings. POST /infer is asynchronous; poll by stable job ID. Queue payloads and results are sensitive application data: protect the state database, backups and host. Retention clears payload/results but retains ID/hash tombstones. A health liveness signal alone is not evidence that the provider is ready.

## Rate changes and exhausted policy

No budget or active-slot reservation is freed solely because time elapsed; verify provider deletion. Full lifetime reservations are conservative and can exhaust the approved cap even when actual usage was shorter. This is intentional, not a claimed billing reconciliation. Raising a cap or changing rates/runtime requires a new exact policy approval. Cost reserves do not guarantee vendor charges cannot differ.
