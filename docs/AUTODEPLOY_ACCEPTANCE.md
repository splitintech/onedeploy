# AutoDeploy acceptance evidence

Engineering preview on `autodeploy`. **No live provider call, registry publication, GPU allocation, production cutover, account setting change or package release was performed during this implementation.** Docker is unavailable in the selected execution environment, so the supplied image was not built locally.

| Capability | Implemented evidence and remaining gate |
| --- | --- |
| Source-bound configuration/plan/CLI | Strict offline tests; existing CLI behavior regression-tested |
| Durable journal, CAS, locks, rollback, safe cleanup | Injected fault tests, including unknown outcomes and secret-safe errors |
| Daytona snapshot/candidate operations | Official 0.231.0 wheel inspected; mock contracts; live echo identity remains unqualified |
| Real worker/control executable | Local-only CUDA faster-whisper code; fake-engine HTTP/state tests; no real CUDA execution |
| Image/model supply chain | Official base manifest/config metadata verified; dependency artifacts hash-locked; image build/CUDA loader/toolbox not run |
| Model volume reuse | Existing account-owned prefix mounted; file allowlist and SHA-256 checked; provider FUSE behavior not tested |
| Stable controller/leases/scale-to-zero | Persistent queue, idempotency, fenced results, candidate adoption, HTTP auth and lifecycle tests |
| Concrete pool transport | Authenticated SDK file/process operations; no payload in argv; full mocked rollout→adoption→job result integration |
| Budget/TTL | Conservative cumulative reservations and active-slot caps, policy/runtime binding, provider hard TTL; rates remain operator estimates |
| Guarded live CLI | Implemented setup/apply/reconcile/rollback/drain/serve, exact approvals and SDK key required; not invoked live |
| GitHub workflow | Pinned actions, exact source, immutable image/plan and opt-in protected rollout job; parsed/static tested, not dispatched |
| Other providers | 20-provider capability roadmap/negotiation; no new vendor falsely marked live-qualified |
| Railway Whisper cutover | Not performed; only inference is in scope, not the entire voice-prompt service |

## Verification records

The first increment passed **640 tests** on Python 3.12 and built wheel/sdist with an installed-wheel CLI smoke test. The follow-on runtime/worker increment passed **701 tests** on Python 3.12, rebuilt wheel/sdist and passed an installed-wheel capability CLI smoke test outside the checkout. Python 3.11/3.13 CI is a separate result, not implied by local 3.12 testing.

Every live qualification gate remains explicit: image build, actual GPU/model output, cold-start latency, shared-volume consistency, account capacity and costs, service-manager/proxy deployment, real traffic drainage and rollback. Tests with injected clients are not evidence of cloud success. Default GitHub workflow rollout is disabled until an operator provisions the durable runner and approved environment policy.

## Independent review fixes

- Deterministic failed GPU evidence no longer masquerades as an unknown write.
- Synthetic qualification stores pending intent and completed evidence, so unknown calls are not replayed.
- Read-only recovery and cost-reducing owner-checked cleanup remain possible after budget exhaustion.
- Candidate lifetime is measured from original creation intent, not controller adoption time.
- Model paths and runtime inputs are bound to reviewed policy; token/audio never enter command arguments.
- Controller dispatch uses desired-release and lease fencing; old-worker drain protects active work.
- Application tests run in an isolated checkout before source-bound plan creation.

## Follow-on qualification (2026-11-11)

Recreated the locked Python 3.12 environment successfully; `pip check` reports no broken dependencies. Independently installed the exact hash-locked worker runtime and imported actual faster-whisper/CTranslate2; this CPU host reports zero CUDA devices. Inspected the official Daytona 0.231.0 wheel's filesystem, process-session, snapshot-resource and hard-TTL signatures against the adapter. This is compatibility evidence, not a provider deployment.

Follow-on regressions cover time elapsed during qualification, executor delays, end-to-end inference setup deadlines and CUDA library discovery before process startup. The image now includes a CPU-only shared-library/import smoke gate. A passing source test does not imply that this image gate has actually run.

**Current external gates:** this connected environment has no Docker/Podman/BuildKit executable or Docker daemon and no GPU. No other connected computer is available. The formal Codex security-scan workflow/preflight is unavailable here; focused code review and regression tests are not a formal scan or certification.

Before a paid live qualification, an operator must provide an approved Daytona target/region/GPU type and a total spending limit (including snapshot/storage/network charges), an existing authorized immutable model volume and checksum manifest, and an authenticated private execution environment. Enter credentials through the provider's secure login or secret store, never a chat or commit. Separately qualify an image build on Docker and real synthetic inference, restart/recovery, drain, rollback, TTL and measured cost/latency. Only after those pass configure the Railway voice-prompt service to forward **Whisper inference only**, retaining its other responsibilities and rollback route.

Follow-on verification: **716 tests passed** on Python 3.12. Actual PyAV decoding accepted a 0.1-second WAV and rejected a 120.1-second WAV before inference. The worker now bounds streamed decoded audio to 120 seconds / 1,920,000 samples and cleanly rejects non-ASCII invalid authorization. Client request deadlines remain observation bounds, not claims of GPU cancellation. Delayed executor tests also cover promotion, draining and deletion before transmission.

## Timeout termination update (2026-11-11)

The prior client-timeout limitation is addressed at the sandbox lifecycle boundary: on a typed job deadline or lease/startup/worker-lifetime expiry, the controller now revokes the result lease and immediately requests deletion of the exact owned dedicated sandbox. It does not attempt unsafe Python thread cancellation. Parallel requests use separate workers; shared-worker concurrency is rejected for this policy. Timeout jobs are not automatically replayed, including under the legacy retry flag.

Candidate qualification timeouts follow a durable termination path. Unknown creation outcomes require read-only identification before cancellation; a recovered timed-out candidate is terminated rather than promoted. Destruction must be confirmed before marking cleanup complete or releasing capacity. If the provider is unreachable, termination remains pending and the finite hard TTL is the final bound. These are source and mocked-contract guarantees, not measured live Daytona cancellation latency or a billing guarantee.

Generic Daytona CLI/SDK creation also requires finite wall-clock expiry. The human and agent/Codex entrypoints share the same OneDeploy lifecycle; no unrelated Codex-managed execution environment is controlled here. Live GPU provisioning remains unperformed.

Final timeout/finite-expiry verification: **761 tests passed** on Python 3.12, including ignored/missing/expired/overlong returned expiry, exact-owned cancellation, unknown outcomes, shared-resource protection and saved-schema contracts. Provider return deadlines are checked against requested TTL (with 30-second clock tolerance), not only against an absolute maximum. No live GPU/provider cancellation latency was measured.
