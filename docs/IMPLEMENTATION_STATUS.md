# Implementation and qualification status

## Current CLI-first transport revision

Daytona now defaults to official CLI 0.226.0 and existing browser-session reuse in the same OS user/configuration context. Optional explicit SDK transport remains available without automatic fallback. Railway/Modal continue using their official CLIs. Guided imports, agent JSON and the manifest path use the same transport selection and source-bound plan engine.

CLI-only Daytona supports private create/build/start, identity/inspection/status/reconcile and unauthenticated probes. Explicitly nonsecret runtime values use process-visible arguments; secrets/default-secret values, authenticated probes and logs require SDK. Tool detection is not proof of authentication. No login, credential migration, real deployment or provider qualification was performed. See the current section in [verification](VERIFICATION.md) and [provider contracts](provider-contracts.md).


This document separates implemented engineering, offline evidence, and live qualification. Version-specific offline evidence and explicit qualification limits are recorded in [verification](VERIFICATION.md). A provider CLI command returning zero is not application readiness.

## Current UX increment versus historical plan

The initial engine and manifest workflow were merged into `main`. `Onedeploy.md` contains the documentation-only proposal that preceded implementation; its proposed wording is historical design context, not the current command reference. The experimental U1–U5 increment adds passive project discovery/resolution, optional saved profiles, a JSON-only agent journey and the opt-in `onedeploy tui --wizard --path ...` interface. Plain `tui` and manifest commands remain the fallback.

Use [the importer contract](IMPORTERS.md) and [agent CLI](AGENT_CLI.md) for exact implemented syntax and limits. Recognition is not complete semantic import, and an importer cannot bypass an adapter's narrower capabilities. [The U1–U5 acceptance matrix](UX_ACCEPTANCE.md) separates implementation from passing tests, usability evidence, live qualification and promotion to default. The final source passed 506 offline tests, independent review and rebuilt-wheel relocation checks for all three providers; exact evidence and unrun external gates are recorded in [verification](VERIFICATION.md).

## Historical baseline plan traceability

| Plan area | Implementation/evidence | Qualification boundary |
| --- | --- | --- |
| Compatibility-first audit | `Onedeploy.md` section 2; `docs/COMPATIBILITY_WORKSHEETS.md` | Source-level assessment only; private application material not copied |
| Generic manifest/config | `onedeploy/config.py`, strict JSON Schema, config tests | Explicit supported schema; no arbitrary provider parity |
| Safe dotenv | Dedicated parser, private-file checks, duplicate rejection, typed variables | No shell expansion; secrets remain local until authorized provider operation |
| CLI and agent interface | `onedeploy/cli.py`, JSON result schema, command tests | Plan ID is intent, not human authorization |
| Immutable planning | `onedeploy/core.py`, canonical plan/source/config binding | Local-only plans cannot execute; explicit read-only remote inspection required |
| Deployment execution | Central bounded subprocess runner and provider adapters | Mutates only after explicit plan approval; no automatic login |
| Journals and uncertainty | Write-ahead private journals, target-scoped local locks, safe no-write recovery plans and uncertain outcome handling | Provider acceptance and cancellation are not rollback guarantees |
| Rollback | Separately approved known-artifact Railway reupload, Daytona replacement sandbox, or Modal release rollback, each with explicit compatibility assertion | Unknown/unretained artifacts and data/schema rollback are refused; replacement does not delete old resources |
| TUI | `onedeploy/tui.py`, Textual headless tests | Guided provider/target creation; masked typed input; prerequisites, saved plans, logs and explicit recovery consent; shared engine |
| Railway | Explicit account/project/environment/service; variable stdin; source upload | Existing configured service required; no implicit project/database/domain creation |
| Daytona | Pinned SDK bridge, project snapshot build, receipt-bound private creation/start, existing volume mounts and process/auth probes | Project Dockerfile snapshot builds and existing shared volumes supported; no implicit volume creation or exclusive-writer guarantee |
| Modal | Explicit workspace/environment/app, trusted Python entrypoint, plan-bound deployment tags and history correlation | Deployment code is executable and must be reviewed; not a sandbox |
| Readiness | Exact deployment attribution; target-associated endpoints; bounded HTTP/localhost readiness and declared read-only negative/positive auth checks | Real authentication, models, latency, durability and application qualification remain operator tasks |
| Templates | Docker HTTP, ASGI, bounded worker, model preparation | CPU model example demonstrates harmless fixed scoring, cache/warmup/concurrency; real weights and base digests require review |
| Packaging | `pyproject.toml`, source/wheel build and isolated-install checks | No registry publication; initial Linux/Python 3.12 verification, CI matrix separate |
| Open-source policy | Existing MIT LICENSE, SECURITY/CONTRIBUTING documents | No private service source or credentials in examples; tested dependency-license inventory recorded |

## Deliberate boundaries, not universal deployment support

- Onedeploy does not create provider accounts, log in, generate or rotate credentials, change DNS, create databases, run schema migrations, seed production data, send messages, or delete provider resources.
- Provider resource existence and account identity must be verified. Private ingress is not inferred from a generic manifest flag.
- Existing Railway volume attachment and Daytona shared read/write mounts are capability-validated. Unsupported read-only/exclusive-writer guarantees and generic Modal mount translation are rejected, not silently ignored. External dependency declarations validate URL/TLS configuration without provisioning or migrations.
- A project Dockerfile can build a Daytona snapshot and then start a receipt-bound sandbox; this does not prove real model weights, GPU compatibility, cache durability, latency or accuracy.
- Saved discovery profiles remain absolute, local-source-bound artifacts. A separate non-executable portable intent export/import carries allowlisted reusable configuration without target IDs, runtime values or approval; another checkout/machine still requires fresh discovery, code trust and destination resolution. Passive discovery uses fixed exclusions and a cooperative traversal deadline, not Git ignore evaluation or a subprocess parser sandbox; the later upload core retains its Git ignore checks.
- Keychain persistence and a local MCP server are optional future integrations, not required to use environment files or the agent-facing CLI. They are not silently provided by the initial implementation.
- Log snapshots and CLI `--follow` snapshot polling are implemented. Polling suppresses identical consecutive snapshots but is not a lossless event stream; bounded `--max-polls` and Ctrl-C stop it.
- Recovery must not automatically replay uncertain writes. Cases that cannot be proven from provider evidence remain blocked for operator reconciliation.
- Live staging and production qualification have not been performed. They require explicit account/resource authorization and cannot be replaced by mocked tests.

## Release evidence

See the [version-specific verification report](VERIFICATION.md) for exact test counts, build/install commands, independent-review findings and unrun checks. Do not infer completion of all plan phases merely from this file or a successful source push.

## Phase-by-phase requirement closure

| Approved phase | Engineering deliverables | Separate gate |
| --- | --- | --- |
| 0: compatibility and contracts | Three sanitized worksheets; strict schema; CLI/result/error/progress contracts; explicit provider capability metadata and trust boundaries | Private service manifests and actual account/application qualification remain owner evidence, not public sample claims |
| 1: safe local core | Safe dotenv/dependency validation; immutable inventory/config binding; approval; bounded subprocesses; private journals/locks; redaction; secret-reference transport | Local host and agent permission policies remain trusted external boundaries |
| 2: Daytona vertical slice | Project Dockerfile snapshot build; immutable receipt handoff; private sandbox create/start; existing shared volume mounts; local readiness/auth checks; logs; receipt-aware recovery and replacement rollback | Real provider build/start/cost/cache behavior requires an authorized staging run |
| 3: Railway/Modal | Explicit Railway source build/upload, existing-volume attachment and retained-bundle rollback; reviewed Modal ASGI packaging, tagged deployment/history binding and known-version rollback; provider-specific status/logs/checks | Account tiers, live API semantics and application ingress remain staging gates |
| 4: TUI | Guided provider/template and target fields; typed masked variables; prerequisite checks; plan save/load/review; progress/cancel; bounded logs; explicit recovery consent, shared core | Terminal behavior tested headlessly; platform/display/accessibility qualification remains explicit |
| 5: resilience | Offline failure/uncertainty/stale identity/config/secret/source/storage/rollback/auth tests and independent review; generic safe checks/model example | Real OAuth, speech/model accuracy/latency, durability and application side effects are not mocked into qualification |
| 6: source release preparation | Fork/install docs, field reference, provider contracts, recovery/troubleshooting, migration policy, license inventory, MIT preservation, packaging/isolated-install tests and credential-free CI | Remote CI execution after reviewed push and registry publication are separate; package publication is not authorized here |

Optional items explicitly allowed by the approved plan to remain future: keychain persistence, a local MCP server, generated thin launchers, agent-specific wrappers and future dotenv interpolation. They are not required to use the generic machine CLI. Arbitrary provider parity and automatic account/database/DNS provisioning are not product promises.

Input-bundle identity is not automatically the identity of all downstream build effects: Docker/provider ignore rules, base images and network dependency downloads can affect the resulting artifact. The plan binds the sanitized inputs and reviewed build program; immutable provider artifact receipts are captured where the supported interface supplies them. This is not a hermetic-build certification.
