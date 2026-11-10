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
