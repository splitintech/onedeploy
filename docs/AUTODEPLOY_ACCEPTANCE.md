# AutoDeploy acceptance evidence

## Scope

Engineering preview on `autodeploy`. No live provider, registry publication, GPU allocation, deployment, account settings, service cutover or package release was performed while developing this change.

| Capability | Evidence/status |
| --- | --- |
| Strict config and immutable release validation | Offline tests |
| Source-bound CLI plans, JSON and compatibility | Offline tests |
| Durable SQLite generation/CAS, locks and journal | Offline tests |
| Timeout recovery, qualification, rollback, safe cleanup | Injected adapter/fault tests |
| Daytona ownership, resource/digest checks and worker protocol | Mock SDK contract tests; official 0.231.0 wheel inspected |
| Reusable GitHub image + plan workflow | YAML/static contract checked; not dispatched |
| Package/source inventory | Local build and checksum tests |
| Authenticated stable router and lease integration | Required from integrating application, not provided as a hosted service |
| Paid budget enforcement and actual autoscaling | Requires trusted host policy/controller; not autonomous merely from configuration |
| Pool admission and cost comparison | Deterministic offline policy tests; advisory only, no background autoscaler |
| Actual GPU inference/cold start/volume behavior | Not run; requires separately approved paid qualification |
| Live CLI apply/reconcile/rollback/drain | Deliberately capability-gated until complete host integration is qualified |
| Railway Whisper cutover | Not performed |

Exact test/build outcomes for this branch are recorded in the PR. A local mocked pass does not certify Daytona GPU behavior. All source references and resource semantics must be revalidated before enabling cloud operations. The design plan describes future completion gates, not a declaration that every stage is already live.

## Local verification, 2026-11-10

- Python 3.12: `python -m pytest -q` → **640 passed**.
- `python deploy/build_sources.py --output <empty-directory>` → wheel and sdist built successfully with checksums, notices and inventory.
- Wheel inspection confirms all eight AutoDeploy modules are included; no state database included.
- `python -m compileall -q onedeploy/autodeploy deploy` and `git diff --check` passed.
- Reusable workflow parsed and static safety contract tested; it was not dispatched.
- Python 3.11/3.13 and live GitHub CI results must be checked separately.

Review fixes included deterministic failed qualification, read-only pending-operation inspection, secret-safe uncertain errors, cost-reducing cleanup after budget exhaustion, and isolated application-test checkout preserving source-bound planning.
