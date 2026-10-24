# Verification report

Checked 2026-10-23. Source version: 0.2.0a1, experimental. Historical baseline evidence and the new UX increment are recorded separately; the baseline count below is not a test result for later changes.

## CLI-first transport revision (2026-10-23)

**Final whole-tree result: 581 passed in 178.57 seconds.** Final runtime source includes the explicit nonsecret injection disclosure and guided transport-selection regressions.

- Default Daytona official CLI contract is pinned to **0.226.0**, the latest official clients release checked on this date. Release/source review confirmed active-organization lookup, JSON shapes, pagination, create flags and per-argument `exec` quoting. No installed provider authentication or deployment command was executed.
- Tests cover default CLI vs explicit SDK, true/missing-secret rejection, explicitly public runtime argument disclosure/redaction, exact target/snapshot/volume binding, bounded output/pagination, identity projection, uncertainty, and guided/agent transport selection. Existing SDK tests run against explicit SDK selection rather than silently changing their backend.
- A real isolated subprocess test uses a fake `daytona` executable and dedicated HOME, forbids SDK import/spec lookup, and rejects ambient API-key/API-URL forwarding. It proves local CLI detection does not require the optional SDK and does not claim authentication.
- Independent review rechecked snapshot receipts for all creates, volume ID/name binding, existing sandbox identity before start, reader completion, and official probe argument quoting. No remaining concrete blocker was found in that review scope.
- Rebuilt wheel installed into a separate directory. With no Daytona SDK installed, CLI-default and explicit SDK adapter selection both load, CLI version command construction succeeds, and all nine installed schema commands emit valid JSON. `pip check` is clean in the test environment.
- Linux/Python 3.12 offline tests only. No macOS/Windows, actual provider login/session refresh, paid resource creation, live startup, network readiness or production qualification is inferred. CLI API-key-only identity, sensitive runtime injection, authenticated probes and logs remain explicit capability limits. See [provider contracts](provider-contracts.md).

## Experimental U1–U5 increment

**Final whole-tree aggregate: 506 passed in 233.98 seconds (3:53).** This run covers the final runtime source after the native-resource portable-binding fixes. Final independent review and the rebuilt installed-wheel checks below also passed. The [U1–U5 acceptance matrix](UX_ACCEPTANCE.md) distinguishes implemented/offline-tested behavior from manual usability, platform and live-provider qualification; these separate gates are not all closed.

- **Final independent review:** no remaining concrete blockers in the reviewed source scope. Recovery-chain profile/source revalidation, env/trust clearing, source-owned portable storage/readiness/snapshot confirmation and direct edited-intent cases were rechecked. Real Compose dependency/storage flows produced matching API/CLI/wizard normalized configuration, with fresh trust enforced. A final focused intent/recovery run passed **43 tests**. This is not a security certification or live-provider qualification.
- **Final rebuilt/reinstalled wheel:** Railway, Daytona and Modal each passed discovery → resolution → profile validation/inspection → offline planning → portable intent export → import into a second directory → a newly bound offline plan. All **nine** installed schema kinds passed, and `pip check` was clean. These checks were repeated after the final source-owned resource-binding correction; no provider/network calls were made.
- **Environment:** Linux, **Python 3.12.14**, **Textual 8.2.8**. Modal import deliberately required fresh app/function confirmation. Other Python/OS environments and remote CI are not inferred from this local result.
- **Contract/regression coverage:** passive supported/unsupported importer subsets; strict schemas, private exclusive outputs, secret references and typed outcomes; source/profile/intent drift; exact destination/trust confirmation; shared planning/approval; unchanged legacy commands; uncertain recovery and profile-origin journals; structured Compose dependency/storage resolution; keyboard/narrow-terminal/resize, cancellation/repeated submission and saved/portable reuse paths.

No live provider call, login, deployment, package-registry publication or real application qualification was performed for this increment. Manual screen-reader/representative-newcomer and macOS/Windows checks remain unrun. The wizard remains opt-in; no promotion to default is implied.

### Documentation and example validation

- The documented minimal Railway/Dockerfile answers were exercised against a fresh synthetic committed Git project: `discover`, `resolve`, `inspect --profile`, `config validate --profile`, an offline `plan --profile`, and the original eight schema kinds completed successfully (13 CLI calls). The provider runner and socket connect were guarded. The source remained unchanged; no Onedeploy manifest was generated; the profile was mode 0600 and the preview was non-executable. An initial non-Git fixture was correctly rejected without `--allow-dirty`; the successful run used a clean Git fixture.
- The portable-intent walkthrough then exercised 15 CLI calls across two source directories, including export, an intentionally incomplete import (exit 11), explicit fresh-target/trust import, offline planning and all nine current schema kinds. Network/provider calls were guarded. Both source trees remained unchanged; old target IDs/absolute root/trust were absent from intent; the resulting profile bound the new source and destination, without deployment authority.
- Relative documentation targets were checked across README, CHANGELOG and 14 docs files (**16 files total**): zero broken local targets. Focused smoke checks are not manual newcomer usability evidence.

## Historical merged-baseline evidence

- Baseline centralized test run: **257 passed in 31.21 seconds**, Linux, Python 3.12.14. Tests block network sockets and isolate provider credentials and home directories.
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

The historical baseline source inventory scan found no populated environment files, private-key markers, common provider credential-token patterns, local state directories or bytecode among publication inputs. This bounded pattern/inventory check is not proof that every possible secret can be detected. Relative documentation links were checked with no broken local targets.
