# Verification report

Checked 2026-10-23. Release candidate: 0.1.0, experimental and offline-tested.

## Evidence

- Final centralized test run: **257 passed in 31.21 seconds**, Linux, Python 3.12.14. Tests block network sockets and isolate provider credentials and home directories.
- Tests cover strict manifests/dotenv/dependency/storage/check contracts, secret handling, sanitized archive/config binding, project snapshot builds and immutable receipts, existing-volume attachments, remote configuration/identity/release correlation, target locks, uncertainty and snapshot-aware recovery, known-artifact rollback, route-specific auth checks, guided TUI flows, JSON schemas and packaged templates.
- Independent review completed a schema-field applicability sweep and inspected the final preflight fixes, finding no remaining concrete blocking defect in the reviewed source-release scope. Review is not a security certification or live-provider qualification.
- Wheel and source distribution built using `python -m build --no-isolation`. Wheel inspected for templates, placeholder environment files and absence of bytecode/cache files.
- Fresh virtual environment installed the wheel with the TUI extra. Outside the source checkout, version/schema checks and guided init/config/offline-plan workflows succeeded for all three providers with synthetic target IDs. The reviewed runtime wheel was reinstalled and those checks repeated; final packaging checks compare the packaged runtime bytes with that installed code. `pip check` succeeded for the fresh installation and full development environment.
- A clean-install check caught generated template bytecode being copied into new projects. Package exclusion and template-copy filtering fixed it, with a regression test and independent re-review.
- Exact provider contracts: Railway CLI 5.71.0, Daytona SDK 0.226.0, Modal CLI/SDK 1.6.2. Railway's official CLI version/help and public command implementation were inspected. Contract tests exercise command construction and fixture responses, not cloud execution.

## Commands to reproduce

From a local fork and virtual environment:

```sh
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m pytest -q
python -m build --no-isolation
```

Install the wheel into a separate virtual environment, change outside the checkout, then check `onedeploy --version`, `onedeploy schema --json`, and the quickstart's template/config/offline-plan workflow. Install the TUI extra to exercise terminal workflows. Do not use real credentials in the offline test run.

## Not verified or not implemented

- No live provider login, account API call, resource creation, deployment, migration, message send, payment or package-registry publication was performed. Live staging acceptance remains an explicit operator-authorized gate.
- Local execution covered Python 3.12 on Linux only. The configured Linux CI matrix also targets Python 3.11 and 3.13; its remote results must be checked after source publication. Windows/macOS compatibility is not established.
- No actual Docker/cloud build, real GPU/model workload, live OAuth/authentication policy, latency, application data durability or production security qualification was performed. The harmless local CPU scoring example and mocked auth-check contracts were tested offline. A successful HTTP response does not prove authenticated ingress.
- Provider capabilities differ. Railway requires an existing configured service and supports explicit existing-volume attachment. Daytona supports project Dockerfile snapshot creation, immutable receipt-bound sandbox creation and existing shared volumes; exclusive-writer/read-only guarantees unavailable in that interface are rejected. Modal runs reviewed, explicitly trusted Python deployment code. External account/database/DNS and automatic volume provisioning are outside scope.
- Railway rollback reuploads a retained known sanitized source/configuration; Daytona creates an explicitly named replacement sandbox from a retained immutable snapshot, preserving the original. Modal rollback requires a separately approved compatible known release; provider acceptance does not establish the identity or readiness of its newly created version. Data/schema rollback is never automatic.
- Uncertain writes are not automatically replayed. Explicit operator reconciliation records an assertion against fresh evidence, not independently proven completion. Plan approval binds intent and does not enforce an external agent's authorization policy.
- Log following uses bounded snapshot polling, not a lossless stream. Optional keychain persistence and a local MCP server are future integrations; the machine CLI is available now.
- Version-pinned dependency resolution files are not a cryptographic supply-chain audit. A bounded dependency-license inventory and compatibility review is recorded in DEPENDENCY_LICENSES.md; no legal or vulnerability certification is claimed.
- OSM, CPU Voice and Slybroadcast remain high-level reference readiness assessments in the approved plan, not applications deployed or certified by this implementation.

See [implementation status](IMPLEMENTATION_STATUS.md), [provider contracts](provider-contracts.md), and [recovery](RECOVERY.md) for the exact supported boundaries.

The final source inventory scan found no populated environment files, private-key markers, common provider credential-token patterns, local state directories or bytecode among publication inputs. This bounded pattern/inventory check is not proof that every possible secret can be detected. Relative documentation links were checked with no broken local targets.
