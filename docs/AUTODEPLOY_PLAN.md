# OneDeploy AutoDeploy: implementation plan

**Status: proposal only, 2026-11-10.** This branch adds a plan, not a working deployment controller, released library, enabled GitHub workflow, or provisioned GPU. Implementation branch: `autodeploy`. OneDeploy remains the project and CLI name. No production migration, credentials, account settings or main-branch merge is part of this documentation change.

## Outcome and boundaries

Build an open-source Python library plus the existing `onedeploy` CLI that makes a reviewed merge to a configured deployment branch trigger:

```text
merge to deployment branch
  → exact-commit tests
  → versioned Docker image and immutable registry digest
  → new Daytona image-derived GPU-compatible snapshot
  → isolated candidate GPU worker and qualification
  → atomic promotion of the desired release
  → drain and remove old workers
```

This is proposed OneDeploy automation around Daytona APIs, not a claim that Daytona already provides Railway-style GitHub branch autodeploy. A Dockerfile defines the runtime; a GitHub Actions workflow triggers the lifecycle. Existing sandboxes do not update themselves when a tag or branch changes.

Initial integration is deliberately narrow: migrate **Whisper inference only** from the Railway voice-prompt CPU path to on-demand Daytona GPUs. Keep the Railway API/orchestrator, authentication, request validation, transcript post-processing, and client-facing interface in place. Cura and Legal Chatbot can share this inference backend through that service. This plan does not establish that both clients are already enabled, or that the local model is actually OpenAI's hosted `whisper-1`; identify the real implementation/model revision before migration. Do not migrate Ozzy's TTS, the complete voice service, databases, or unrelated applications.

## Source baseline and Ozzy reuse

Inspected public source:
- OneDeploy main: `2c97ac8bab41d20a9b579d8db732f6bb597349d3`. Existing Python 3.11+ setuptools library, `onedeploy.cli:main`, pinned `requirements-dev.lock`, offline pytest/build CI, typed plans, CLI-first Daytona bridge and explicit SDK path. Keep the existing Railway/Modal contracts unchanged.
- Ozzy main tree response: `e95330bce199b9959b60f82ff7ac5f167b076a4e`. Read `README.md`, `pyproject.toml`, `.github/workflows/ci.yml`, `.github/workflows/containers.yml`, `deploy/Dockerfile`, and `deploy/build_sources.py`.
- Ozzy's container workflow currently builds CPU images manually. It is a build-style reference, **not an already implemented GPU autodeploy workflow**.

Reuse Ozzy's principles: pinned action commits and base-image digests, locked dependencies, separate lightweight controller and inference images, non-root execution, explicit entrypoint/toolbox compatibility, source/wheel allowlists, SBOM and license inventory, immutable model checksums, documented preview limitations, separate offline and paid cloud gates. Preserve MIT notices for any code actually copied and review third-party model licenses separately.

Adapt, do not duplicate: OneDeploy has no need for Ozzy's desktop/Tauri, browser app, FFmpeg distribution, Chatterbox weights, PostgreSQL single-sandbox profile, or Node build unless a future feature genuinely needs them. Preserve OneDeploy's package layout and Python compatibility instead of migrating it into Ozzy's monorepo. README should use the same clear install → first use → profiles → operations → recovery → verification style, with OneDeploy commands and truthful status.

## Proposed file map

These files are proposed; this document does not install them.

| Path | Responsibility |
| --- | --- |
| `onedeploy/autodeploy/models.py` | Versioned deployment, release, worker and policy schemas |
| `onedeploy/autodeploy/planner.py` | Source-bound immutable rollout plan and budget validation |
| `onedeploy/autodeploy/reconciler.py` | Idempotent lifecycle, retries, reconciliation and recovery |
| `onedeploy/autodeploy/state.py` | State-store interface, atomic compare-and-swap and audit records |
| `onedeploy/autodeploy/routing.py` | Stable-service routing interface and worker leases |
| `onedeploy/autodeploy/daytona.py` | GPU/snapshot adapter reusing existing provider abstractions |
| `onedeploy/autodeploy/cli.py` | Subcommands registered in the existing CLI |
| `onedeploy/schemas/autodeploy.schema.json` | Strict, versioned configuration validation |
| `examples/daytona-gpu-autodeploy/` | Generic worker, Dockerfile, example config, synthetic smoke request |
| `examples/whisper-worker/` | Optional pinned STT adapter, no application secrets or production audio |
| `deploy/build_sources.py` | Adapted package integrity, SBOM and notices build |
| `.github/workflows/autodeploy.yml` | Reviewed reusable workflow, initially opt-in |
| `tests/autodeploy/` | Offline, fault-injection, CLI, state and provider-contract tests |
| `docs/AUTODEPLOY_QUICKSTART.md` | Human/agent setup, CLI reference, permissions and costs |
| `docs/AUTODEPLOY_OPERATIONS.md` | Deploy, scale, drain, rollback and incident recovery |
| `docs/AUTODEPLOY_ACCEPTANCE.md` | Exact-SHA evidence, tested versions and unresolved qualification |

Keep core imports free of Torch and provider side effects. Optional dependencies isolate GPU/application runtimes. Export a typed Python API equivalent to CLI plan/status/reconcile/rollback operations; keep the provider adapter replaceable so Daytona can adopt or fork it without SplitIn application dependencies.

## State and worker contracts

A release record binds deployment ID, target account/project, source repo + exact commit, configuration digest, immutable image digest, snapshot ID, model manifest digest, runtime/CUDA versions, resource request, test evidence, creation time and rollout generation. Store no credentials, raw audio or signed preview URLs in public manifests.

Use a durable controller-side state store supporting transactions/CAS; do not rely on GitHub artifacts or an ephemeral GPU filesystem for authoritative routing. A persistent single-controller deployment may initially use SQLite with an exclusive owner; multi-controller operation requires a transactional shared store and must be rejected until supported.

State machine:
`planned → built → snapshot_ready → candidate_ready → qualified → promoted → draining → complete`.
Failures become `failed` or `needs_reconciliation`, never automatic success. Persist provider resource IDs immediately. Use unique operation IDs, owner tags and fencing tokens; a timeout is an unknown outcome to inspect, not permission to create duplicates.

Workers provide separate liveness, readiness and capability/version endpoints plus authenticated inference, in-flight counters, drain and shutdown. Readiness includes CUDA presence, actual requested model on GPU, checksum verification, model load and bounded synthetic inference. HTTP 200 alone is insufficient. No application request should execute arbitrary commands.

A stable controller address routes requests to leased workers. Clients never persist ephemeral sandbox URLs. Promotion atomically changes the desired release pointer; new jobs target ready workers for that release. Existing jobs stay on their assigned worker until completion or an explicitly documented timeout/requeue policy. Use job IDs and idempotency to avoid duplicate externally visible outputs.

## Step-by-step rollout

1. **Inventory and qualify API support.** Confirm currently supported Daytona SDK/CLI versions, GPU types/capacity, snapshot creation and registry access, process bootstrap, volumes, authenticated networking and deletion semantics. Existing OneDeploy pins Daytona 0.226.0; do not assume it supports all required GPU operations. Upgrade only with compatibility tests and migration notes. CI uses an explicitly selected headless SDK transport where needed; preserve local CLI-first behavior and never silently switch credentials/transports.
2. **Configure once.** Operator selects repository, exact deployment branch, provider/project, registry, stable controller, approved resource limits and budget, secret references, model manifest and rollback policy. Verify account quota and ability to pull private images. Startup-program access does not prove GPU entitlement, capacity or free credits. Provisioning remains separate from this plan.
3. **Trust gate.** Branch push after reviewed merge triggers exact-SHA checkout. Fork PRs run offline tests without secrets; never run untrusted PR code under `pull_request_target` with deployment credentials. Require selected status checks and protected environments before enabling automatic release. Existing branch protection is not assumed. Bind standing deployment policy to allowed repo/branch, target and spending limits.
4. **Test.** Run existing Python 3.11/3.12/3.13 tests, package build and CLI help; add schema, adapter, reconciliation and example tests. Pin actions by reviewed commit. Application-owned qualification commands run only from trusted commits. Failure stops before publishing or provisioning.
5. **Build once.** Build an allowlisted Docker context with CUDA-compatible pinned base, non-root user and Daytona-compatible initialization. Build and smoke-test CPU-observable parts in CI, generate SBOM/provenance and scan policy, push a unique commit/version tag to the approved registry. Record immutable digest. Never deploy `latest` or bake secrets into build args/layers.
6. **Create snapshot.** Reconcile a deterministic snapshot name from release/image identity; create from the image digest and wait for ready state with bounded timeout. A GPU snapshot here means an image-derived compatible runtime, not saved live GPU memory. Record failure and clean only resources owned by this release.
7. **Candidate worker.** Start one budget-bounded isolated GPU worker with explicit CPU, RAM, disk, GPU count/type and model-volume revision. Verify registry access, runtime startup, model checksum, correct GPU execution, synthetic STT contract, latency/error limits and stable-controller authentication. Candidate never receives ordinary production traffic before qualification.
8. **Promote.** Acquire deployment lease; check intended release is still latest using CAS and a monotonic generation. Reject stale CI even if it finishes after a newer release. Publish qualified desired release and route new jobs only after acknowledgement. If scaled to zero, persist desired release for the next request rather than leave a GPU running.
9. **Drain.** Mark old workers unavailable for new jobs; wait for active job leases, with bounded deadline and defined retry semantics. Do not delete healthy old capacity before replacement is ready. Never delete shared model volumes or previous release snapshots during worker cleanup.
10. **Observe and close.** Log source SHA, snapshot, timestamps, qualification evidence, estimated/observed resource time, state transitions and cleanup status. Alert on errors and orphan resources without recording user transcripts. Retain bounded release history and rollback assets.
11. **Rollback.** Atomically select the last qualified compatible release, reconcile new workers from its immutable snapshot, verify ready, switch new traffic, then drain failed-version workers. No automatic schema rollback or destructive data migration. If capacity is unavailable, keep old healthy workers rather than claim successful rollout.

## Cost-efficient on-demand design

Default to a **small bounded reusable worker pool with scale-to-zero**, not one new sandbox per utterance. Reuse a healthy GPU for short bursts, allow concurrent jobs only within measured VRAM/latency limits, and grow the pool only within configured maximum workers and budget. Compare per-turn isolation experimentally rather than assume it saves money.

Persist model weights once as checksum-verified versioned artifacts on an approved shared volume or object store; mount/read them immutably, optionally stage to local disk. Use one writer and atomic publication to avoid corrupt concurrent downloads. Shared storage does not share loaded RAM/VRAM: each worker loads its own model. Include FUSE/storage throughput and cold-start costs in measurements.

Record startup, download/cache load, model load, inference, idle and drain seconds. Compare:
- per-turn cost ≈ requests × (startup + model-load + inference + cleanup) × total resource rate;
- pooled cost ≈ worker startup/load events + inference + paid idle/drain time, multiplied by each worker's total rate.

Account for GPU **and** CPU/RAM/disk, registry/snapshot retention and candidate overlap. Require max workers, max startup attempts, candidate TTL, idle timeout, per-job limits and daily budget guardrails. Measure p50/p95 latency and cost with actual traffic estimates. Choose the shortest idle grace that meets the agreed latency SLO; no universal cheapest timeout is asserted.

GPU lifecycle is recreate-on-demand, not pause/resume of loaded VRAM. Explicitly validate deletion/idle semantics against current docs. Do not inherit large default resource allocations. Preserve a manual rollback path to Railway inference until GPU qualification and limited rollout pass; only then remove unused CPU inference dependencies/resources, not the voice-prompt service itself.

## CLI and README contract

**The following commands are proposed, not available today:**

```text
onedeploy autodeploy init --provider daytona --output autodeploy.yaml
onedeploy autodeploy validate --config autodeploy.yaml --json
onedeploy autodeploy plan --config autodeploy.yaml --commit <sha> --json
onedeploy autodeploy apply --plan <reviewed-plan-id> --json
onedeploy autodeploy status --deployment <id> --json
onedeploy autodeploy reconcile --deployment <id> --json
onedeploy autodeploy rollback --deployment <id> --release <id> --json
onedeploy autodeploy drain --deployment <id> --release <id> --json
```

A plan ID is not authorization. Interactive apply confirms exact material effects; CI apply requires an explicit trusted policy matching the source, destination and limits. CLI and Python API share validation and operation semantics, structured exit codes and redaction. Machine stdout is JSON only; progress goes to stderr. Include stable request/release IDs, typed blocked/retryable outcomes and suggested recovery actions.

README structure matching Ozzy:
1. Clear OneDeploy identity, useful one-line purpose and truthful preview status.
2. What it can do and what remains unsupported.
3. Prerequisites and install from reviewed checkout; do not claim a registry package exists.
4. First preview and first authorized deployment, with copyable tested commands.
5. Profiles: local/offline, CI, on-demand GPU and warm pool.
6. CLI + Python quickstarts, agent JSON workflow and configuration reference.
7. Start/status/update/drain/rollback examples and costs.
8. Security, durable state, observability, troubleshooting and model licenses.
9. Verification table separating offline tests, paid GPU qualification and production acceptance.
10. Contributing, MIT attribution, version policy and adoption/fork guide.

## Security and reliability acceptance

- Tests prove secrets never enter CLI arguments, build context, artifacts, plan JSON or logs. Daytona credentials remain in CI/controller secret storage; short-lived application auth is rotated and scoped. Never expose admin tokens to workers or browsers.
- Reject unknown schema fields, mutable images, path traversal in build contexts, unsafe symlinks, untrusted deployment targets and unsupported resource settings.
- Concurrent releases, stale promotion, CI cancellation, API timeout-after-success, candidate failure, quota exhaustion, disk exhaustion, worker crash, model corruption and network partition all have reproducible fault-injection tests.
- No new traffic on draining workers; no lost completed results; bounded retries with idempotency. Restore from durable release state after controller restart.
- Cost limits prevent runaway spin-up. Orphan cleanup only matches project/deployment ownership labels and safe retention rules.
- GPU qualification separately verifies CUDA/cuDNN/model compatibility, correct placement, model limits, memory saturation, cold start and workload-specific output. Never treat offline mocks as live certification.
- Existing Railway/Modal and local Daytona flows remain green. No automatic credential migration or provider fallback.
- Supply-chain check includes pinned dependencies/actions/images, SBOM, notices, package content allowlists, signed/provenance-bearing release artifacts where supported.
- No real user audio in CI. Define retention and deletion for transient STT files and prohibit transcript contents in metrics.

## Delivery milestones and exit gates

1. **Design approval:** agree state/routing/storage contracts, source policy and resource budgets; publish schema and ADRs.
2. **Offline engine:** library, CLI, fake adapter and deterministic state-machine tests. Exit: package/import/CLI tests pass; no cloud side effects.
3. **Artifact pipeline:** reproducible image, SBOM, registry digest and snapshot adapter. Exit: image integrity and secret-scan checks pass.
4. **Paid provider qualification:** explicitly budgeted disposable candidate, GPU evidence and cleanup. Exit: capacity, model correctness, recovery and billing observations documented.
5. **Progressive rollout:** synthetic/internal requests, limited real traffic with authorization, then full inference-only migration. Exit: agreed latency/error/cost SLOs and rollback drill pass.
6. **Public preview:** reviewed README, examples, API/CLI compatibility, license review, release notes and known limits. Submit upstream proposal without claiming adoption.
7. **Stable release:** supported versions matrix, operational ownership, clean-environment install and repeated recovery/rollback evidence.

No date or performance promise substitutes for these gates. This plan does not enable automatic deployment until implementation, policy configuration and provider qualification are complete.

## Daytona contribution path

Offer a reusable library/reusable Actions integration, or invite Daytona to fork/adopt the feature. Keep SplitIn-specific Whisper configuration in examples. State that SplitIn has Daytona startup access as user-reported context, not partner endorsement or validated GPU credits. Ask maintainers which repository/integration surface they prefer and what API, compatibility, security and maintenance requirements apply. Do not promise support commitments or transfer ownership without separate agreement.

## References and implementation-time revalidation

- [Daytona GPU sandboxes](https://www.daytona.io/docs/en/gpu-sandboxes/)
- [Snapshots](https://www.daytona.io/docs/en/snapshots/)
- [Git operations](https://www.daytona.io/docs/en/git-operations/)
- [Linked accounts](https://www.daytona.io/docs/en/linked-accounts/)
- [Daytona webhooks](https://www.daytona.io/docs/en/webhooks/) are lifecycle events, not proof of a GitHub branch deployment trigger.
- [GitHub Actions events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [Ozzy build reference](https://github.com/splitintech/ozzy/blob/main/deploy/Dockerfile)
- [Ozzy verification style](https://github.com/splitintech/ozzy/blob/main/README.md)
- [OneDeploy provider contracts](provider-contracts.md) and [security](../SECURITY.md)

Revalidate SDK signatures, quota, pricing, resource defaults, volume/region support, authentication and GPU lifecycle against current documentation before implementing or spending money.
