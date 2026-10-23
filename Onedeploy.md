# Onedeploy: implementation plan

Status: **plan only**. No implementation, package release, cloud deployment, account login, credential setup, migration, or live application action is represented by this document.

## 1. Product goal

Onedeploy is a standalone, open-source developer tool that users can fork, install, and run locally to deploy their own services through Railway, Daytona, or Modal. It combines an interactive terminal interface with a stable machine-readable CLI that any agent capable of invoking local commands can use.

The tool is project-agnostic. It does not depend on SplitIn application code, accounts, infrastructure, credentials, or an LLM provider. OSM Map Studio, Voice, and Slybroadcast are initial compatibility case studies, not dependencies or hardcoded product modes.

**Promise:** one workflow for supported deployment profiles, with explicit provider differences.

**Not a promise:** every arbitrary application can run unchanged on every provider. Onedeploy must identify unsupported combinations before making changes and explain the missing packaging or capability.

## 2. Compatibility audit comes first

### Audit scope and evidence quality

The initial source/documentation assessment reviewed OSM Map Studio, the CPU Voice-to-Prompt service, and Slybroadcast against an identified source revision. This was a read-only architecture assessment, not deployment qualification. No live provider resources, credentials, billing, migrations, real audio, OAuth sessions, or message delivery were tested.

Private source, configuration, account identifiers, endpoints, and deployment scripts are deliberately not reproduced in this public plan. Any public example must be independently written with synthetic values. An implementation audit should record exact revisions and evidence in the owner's private release record without requiring that record to be published.

### Initial readiness assessment

| Service | Existing foundation | Ready for an Onedeploy integration? | Missing before live qualification |
| --- | --- | --- | --- |
| OSM Map Studio | Containerized frontend/API, configuration preflight, image/snapshot and Daytona provisioning workflow | Suitable initial container-service case study; not an existing Onedeploy integration | Stable browser OAuth origin, storage/schema prerequisites, authenticated ingress, actual owner-login acceptance and provider-specific lifecycle checks |
| Voice-to-Prompt | CPU speech runtime, immutable-image Daytona lifecycle, private ingress, model-cache and dependency checks | Suitable stateful model-service case study; integration still needs implementation | Qualified model cache and warmup, external database/Redis TLS, user authentication through its BFF, resource/latency tests, cache ownership and recovery tests |
| Slybroadcast MCP | Private HTTP MCP service, exact-revision Daytona startup, authentication and discovery checks | Suitable authenticated ASGI/MCP case study; not universal provider support | Secure credential delivery, expiring endpoint lifecycle and client reconnection, provider qualification; permanent endpoint options need separate packaging |

Important distinctions:

- **Voice means CPU Voice-to-Prompt**, not a separate Legal/OpenAI realtime voice implementation. Profiles must not silently switch models or products.
- Map Studio's process readiness is not proof of database migrations, bucket policy or OAuth correctness. Public map delivery remains a separate application decision.
- Voice depends on an external database and Redis plus a persistent model cache. A generic stateless container profile is insufficient. Multiple active writers to one cache prefix must be rejected unless the application explicitly supports them.
- Slybroadcast deployment must default to read-only application behavior. Health/authentication/MCP initialization are appropriate smoke tests; sending messages or campaigns is not.
- An older separate Modal Slybroadcast prototype exists, but it is not equivalent to the current service or a live-qualified adapter. Reconciliation and new testing are required before reuse.
- None of the three is currently “Onedeploy-ready” in the sense of having an implemented, tested Onedeploy manifest and provider integration. They are integration candidates with reusable architectural patterns.

### Initial service/provider assessment

| Case study | Daytona | Railway | Modal |
| --- | --- | --- | --- |
| OSM Map Studio | Existing operator foundation; integrate without weakening stable-origin/auth controls | Candidate container-service profile; new integration and qualification required | Not a first-release priority; new adaptation required |
| CPU Voice | Existing lifecycle foundation; cache, private ingress and explicit starts matter | Candidate after persistent-cache/resource/auth profile is qualified | New model lifecycle, concurrency, cold-start and persistence design required |
| Slybroadcast MCP | Existing foundation; expiring signed endpoints are not permanent hosting | Candidate stable HTTP-service profile; new packaging required | Separate prototype only; reconcile service core and qualify before support |

These describe observed integration status and design priorities, not claims that providers have identical capabilities or that any account is ready.

### Phase-zero deliverable

Produce a compatibility worksheet for each target service/profile: source revision, build context, startup command, port, secret variables, external dependencies, ingress/auth requirements, persistent data, readiness semantics, lifecycle constraints, safe smoke checks, rollback boundary and unsupported providers. Obtain missing evidence before marking a combination supported.

## 3. User experience

### Local developer workflow

1. Fork or clone the public repository and install the tool in an isolated environment.
2. Run `onedeploy init` to select a generic template and create a manifest plus placeholder `.env.example`.
3. Put local configuration in a private `.env` file or use existing environment/secret-manager references.
4. Run `onedeploy tui` for guided setup, or use the same engine through CLI commands.
5. Select a provider, account context, project/environment and explicit existing or new resource.
6. Validate packaging, required variables, capabilities and prerequisites.
7. Review a saved deployment plan showing exact source/artifact, destination, resources, ingress, secret destinations and expected changes.
8. Approve that exact plan and deploy. Watch structured progress and verification.
9. Receive a nonsecret result summary and instructions for any protected connection output.
10. Inspect status/logs, resume a reconciled interrupted operation, or explicitly plan rollback.

A user can run one command or an optional generated thin launcher for a service. The implementation remains one shared package, not many duplicated deployment scripts.

### Agent workflow

Agents use the same commands with `--json` and no interactive prompts. There is no built-in LLM requirement and no privileged “agent bypass.”

Illustrative command contract, to be finalized and versioned during implementation:

```text
onedeploy init --template docker-http
onedeploy config validate --manifest onedeploy.yaml --env-file .env --json
onedeploy inspect --manifest onedeploy.yaml --json
onedeploy plan --manifest onedeploy.yaml --env-file .env --out plan.json --json
onedeploy deploy --plan plan.json --approve-plan <plan-id> --env-file .env --json
onedeploy status --deployment <deployment-id> --json
onedeploy logs --deployment <deployment-id> --follow --json
onedeploy resume --deployment <deployment-id> --plan-out recovery-plan.json --json
onedeploy rollback --deployment <deployment-id> --to <release-id> --plan-out rollback-plan.json --json
onedeploy doctor --provider railway --json
onedeploy schema --json
```

`resume` and `rollback` initially inspect and produce a new plan; execution goes through the same approval boundary. No command should turn an ambiguous previous operation into an automatic retry.

## 4. Package architecture

### Proposed stack

- Python 3.11+ core, chosen to work naturally with provider SDK ecosystems and local automation.
- Textual for the full terminal UI, layered over the public engine API.
- A typed CLI framework and validated models, selected and pinned after dependency compatibility testing.
- Declarative YAML manifests with a strict schema; JSON input/output for agents.
- Provider SDKs or official CLIs behind adapters, selected per operation rather than assuming one universal transport.
- Optional keychain integration. No automatic credential storage.

Proposed layout:

```text
onedeploy/
  cli/
  tui/
  core/          # validation, planning, policy, execution, reconciliation
  config/        # manifest schema, safe dotenv parser, secret references
  providers/     # railway, daytona, modal
  state/         # journals, locking, release metadata
  templates/     # independent generic examples
schemas/
tests/
docs/
examples/
```

### Separation of concerns

1. **Manifest:** what the application requires and which artifacts to run.
2. **Provider adapter:** what the selected platform can do and how to perform a supported operation.
3. **Planner:** resolves inputs and capabilities into an immutable, inspectable change set.
4. **Executor:** executes only that approved change set with bounded subprocess/SDK behavior.
5. **Verifier:** checks explicit process, readiness, ingress and application-safe acceptance conditions.
6. **Journal/reconciler:** records intent and observations, then resolves uncertain outcomes against actual provider state.
7. **CLI/TUI:** alternative interfaces to the same engine and policy, never separate implementations.

## 5. Manifest and configuration contract

A manifest describes:

- Schema version and application name.
- Runtime profile: container HTTP, ASGI HTTP, bounded worker, or model service.
- Source/build context and immutable source revision or artifact digest.
- Startup command as an argument array, never evaluated shell text.
- Port, health/readiness paths and bounded verification timeouts.
- Variables: type, required/conditional status, sensitivity, validation and safe defaults.
- External dependencies, persistent-storage requirements and single-writer constraints.
- Supported provider profiles and provider-specific resource/ingress/lifecycle settings.
- Safe post-deployment checks and rollback compatibility requirements.

Parse YAML with a safe loader that rejects duplicate keys, unsafe/custom tags and unexpected schema fields. Reject excessive nesting or oversized inputs before validation.

Application build and startup commands are executable code supplied by the user. Onedeploy must disclose this trust boundary, restrict arbitrary hooks by default, and never imply manifests from unknown repositories are safe merely because they validate structurally.

### `.env` handling

- Use a dedicated parser, not `source`, shell evaluation, `eval` or command substitution.
- Initially accept a documented small grammar: key/value pairs, comments, quoted strings and explicit escapes. Reject ambiguous multiline forms and unsupported syntax with line numbers but without values.
- No implicit `${...}` interpolation or command expansion. If future interpolation is added, make it explicit and bounded.
- Reject duplicate keys by default to avoid ambiguous precedence.
- Define deterministic precedence: explicit selected secret source/environment override, supplied env file, manifest nonsecret defaults. Explain precedence in validation output without disclosing values.
- Never discover and load arbitrary ancestor `.env` files silently.
- `.env`, connection output, journals containing protected metadata and local credentials stay outside version control; private permissions and symlink checks apply.
- Separate provider control-plane credentials from application runtime variables using allowlists. Never forward the entire local environment.
- Never send secrets as command-line arguments or include them in plans, source bundles, Docker layers, tracebacks or analytics.

A plan stores secret references and variable names, not values. Apply resolves them locally. When values have changed since planning, require an updated plan or an explicit documented revalidation mechanism. Do not expose plain secret hashes that allow guessing low-entropy values; use an appropriate local keyed binding where required.

## 6. Provider capability model

Each adapter declares versioned capabilities: supported profiles, artifact types, resource targeting, ingress modes, persistent storage, lifecycle operations, logs, deployment observation, rollback support and secret-delivery methods.

### Railway adapter

Target container/web-service workflows with explicit project, environment and service selection. Separate building/uploading from readiness and domain configuration. Prefer existing authenticated CLI context or scoped credentials through supported secure mechanisms. Do not silently create a project, link the wrong service, add databases, change domains or modify public exposure.

### Daytona adapter

Treat deployment as sandbox/image/snapshot lifecycle, not an always-on web-service equivalent. Expose idle-stop policy, restart behavior, persistent-volume assumptions and private/signed/public ingress differences. Expiring signed URLs and sandbox-scoped credentials are distinct. Require explicit stable-origin design for browser OAuth. Inspect before retrying uncertain creates. Do not automatically delete recovery state or introduce request-time paid auto-wake.

### Modal adapter

Treat deployment as an application/function runtime with explicit entrypoint, image, secrets, concurrency, startup and scale-down behavior. ASGI and model-service templates require provider-specific packaging. Persistent model data and cold starts need explicit design. No inference that an arbitrary Docker daemon, long-lived worker or filesystem layout maps directly to Modal.

### Support levels

Use separate labels: `unsupported`, `experimental/offline-tested`, `staging-qualified`, and `production-qualified-by-operator`. Package release status must not imply a user's application has been production-qualified. Verify official APIs/CLI flags and pin/test compatibility versions before shipping each adapter.

## 7. Plans, approval and recovery

A plan binds the schema version, adapter version, source revision/digest, target account/project/environment/resource, config structure, secret references, resources, costs/exposure notes, operations and verification steps. Canonical serialization produces a plan ID. Any material target or operation change invalidates approval.

`--approve-plan` binds execution intent to the exact plan; knowing or supplying its ID is not proof of human approval or a security authorization. A machine caller needs an explicitly configured execution policy or a trusted approval boundary outside the agent's own assertion. An agent must not treat its ability to read a plan ID as permission to approve itself. The local CLI cannot enforce every external agent platform's permission policy; integrations must preserve that policy and document who can authorize execution.

A source commit alone does not identify a local upload. Reject dirty/untracked build inputs by default, or explicitly inventory and bind every included file to the plan. Hash the actual filtered build-context/upload bundle, including relevant untracked files and ignore rules, and recheck it immediately before upload. Never claim an artifact matches a reviewed commit when local modifications are included.

Immediately before apply, revalidate the authenticated provider account, exact project/environment/resource identity and planned remote preconditions. Changed credentials, resource configuration, ownership or deployment generation must fail closed and require reconciliation/replanning rather than silently targeting a different destination.

Dry-run planning must not mutate cloud state or write runtime secrets. Clearly distinguish purely local planning from optional authenticated read-only inspection. Agents receive `approval_required` rather than an interactive prompt when execution lacks explicit approval.

Write a private, atomic journal before each consequential provider request. Record intent, operation ID, known resource IDs and result state, but no secrets. Lock a deployment locally to avoid concurrent execution; document that local locking is not a provider-wide transaction.

Possible operation states include planned, awaiting approval, running, succeeded, failed, cancelled and uncertain. A timeout or lost connection after a request is **uncertain**, not failed-with-no-effects. Reconcile by stable provider IDs and labels; do not blindly rerun resource creation, deployments or application actions.

Retries are bounded and classified. Safe reads may retry; writes require proven idempotency or outcome reconciliation. Cancellation stops new work and reports what may still be running remotely. It must not imply resources were deleted or costs stopped.

Rollback creates a separate plan targeting a known prior compatible artifact/configuration. It does not undo schema migrations, delete data, weaken auth, revoke unknown credentials or replay side effects. Unsupported rollback returns a clear refusal and recovery guidance.

## 8. Security and execution boundaries

- Spawn subprocesses using argument arrays with `shell=False`, explicit cwd, minimal environment, timeouts and bounded output.
- Validate executable selection, paths, URLs and identifiers. Do not accept arbitrary executable overrides from untrusted manifest data.
- Redact structured fields and known secret values, including URLs with credentials. Prefer not collecting sensitive output over relying only on redaction.
- Provider logs can contain application secrets or personal data. Make retrieval explicit and warn that redaction is best-effort; never upload logs automatically.
- No telemetry by default. No credential collection server.
- Existing provider login may be used locally; initial login, credential creation and persistence remain explicit user actions.
- Warn before transmitting runtime secrets to a provider and identify the destination. Control-plane tokens never enter application containers.
- Do not automatically apply migrations, seed databases, enable public access, send messages, register OAuth apps, change DNS, or purchase services as hidden deployment steps.
- Cost estimates and resource limits are not guaranteed spending caps. Show known ongoing storage/compute implications and unresolved estimates.
- Third-party plugins run code with local permissions: explicit installation/trust is required. No automatic plugin download from manifest URLs.
- No private application source, credential-bearing examples or internal endpoints in the public repository.

## 9. Machine interface and optional agent integrations

Every command offers a versioned JSON envelope: schema version, command, status, result or structured error, warnings and next actions. Errors include stable codes, retryability, uncertainty and whether any mutation may have happened.

Progress can use newline-delimited JSON events with sequence number, timestamp, deployment/operation IDs, phase and redacted payload. Send machine output only to stdout; diagnostic human text goes to stderr. Test interruption, partial output and terminal/nonterminal behavior.

Define stable exit codes for success, invalid configuration, unsupported capability, approval required, authentication failure, provider failure, uncertain operation and cancellation. Publish JSON Schemas and representative fixtures.

Any local coding agent can invoke the CLI. Agent-specific wrappers are optional examples, not core dependencies. An optional local MCP adapter may expose plan/status/deploy tools later, but must call the same engine and preserve plan approval. It is not needed for the first useful release and must not become a remotely accessible privileged deployment server by default.

## 10. Generic templates

Publish independently written examples with placeholder values only:

1. Docker HTTP application with liveness and readiness.
2. Python ASGI service with explicit auth configuration placeholders.
3. Bounded worker, available only on qualified provider profiles.
4. CPU model service illustrating cache, warmup, concurrency and dependencies.

Templates document prerequisites rather than creating external databases, identities or secret accounts automatically. Example application behavior must be harmless and runnable offline. Service-specific launchers merely pass a manifest path to the shared CLI.

## 11. Implementation phases and acceptance gates

### Phase 0: compatibility and contract design

Complete the three case-study worksheets, generic capability matrix, threat/trust model, manifest schema, command contract and explicit unsupported cases.

**Gate:** no “ready” claim without identified evidence; no private application material in public artifacts. Review this plan before implementation resumes.

### Phase 1: safe local core

Implement manifest/dotenv validation, secret references, immutable plans, approval checks, journal/locking, redaction, mock executor and machine CLI. Start with a small dependency set and lock development/test environments.

**Gate:** all tests are offline; invalid input has zero provider calls; no secret values in plans/results/errors; changed plans reject old approvals; uncertain writes cannot be blindly retried.

### Phase 2: first vertical provider integration

Build one end-to-end provider/profile path using an official supported interface and generic example. Daytona is the initial reference because the case studies have existing architectural foundations, not because private helpers may be copied into open source. Verify interface versions before implementation.

**Gate:** contract tests exercise actual command/SDK construction, parsing, failure and uncertain outcomes. A real authorized staging run is separately required to mark it staging-qualified; mocks alone remain offline-tested.

### Phase 3: Railway and Modal adapters

Add Railway container HTTP and Modal ASGI profiles, each with its own packaging, ingress, secret delivery and lifecycle behavior. Add status/logs and reconciliation through real supported interfaces. Unsupported operations fail explicitly.

**Gate:** provider-specific fixtures and capability rejection tests; no fabricated resource IDs or simulated successes returned as actual deployments. Each live qualification remains an explicit operator task.

### Phase 4: interactive TUI

Build guided service/provider selection, typed variable prompts, masked secret entry, prerequisite diagnosis, plan review, progress, cancellation and recovery screens on the same engine. Include accessible keyboard navigation and narrow-terminal behavior.

**Gate:** interactive and machine flows produce equivalent plans. Back/cancel/retry do not create duplicate operations, leak values or reset approval state.

### Phase 5: integration and resilience

Test interrupted requests, stale plans, wrong account/project, expired credentials, unsupported runtime, build failures, readiness failures, malformed provider responses and conflicting active deployments. Develop generic readiness recipes inspired by the audited services, with no private source copied.

**Gate:** independent review; exact scope of all passed, failed and unrun checks documented. No production compatibility badges based only on mocked tests.

### Phase 6: open-source release preparation

Complete installation/fork instructions, contribution/security policies, examples, versioning, migration policy, supported versions, changelog and licensing. The repository already contains an MIT LICENSE supplied in its initial commit. Preserve that license and complete dependency license-compatibility review before release.

**Gate:** clean local install, package build and offline CI pass on the supported Python/platform matrix; artifact and secret scans pass; repository branch reviewed. Package registry publication and production deployment are separate actions, not implied by a source push.

## 12. Test strategy

- Unit: dotenv grammar, duplicate/ambiguous values, conditional variables, schema validation, capability resolution, canonical plan identity and redaction.
- Security: injection-shaped values, malicious paths/symlinks, oversized provider output, credential-bearing URLs, accidental environment forwarding and logs/tracebacks containing synthetic secrets.
- Contract: official CLI argv/SDK models and representative response parsing for pinned supported versions.
- Recovery: timeout after provider acceptance, crash before/after journal write, cancelled build, process signals, repeated resume, stale resource identity and concurrent local invocations.
- CLI: JSON schema stability, exit codes, stdout/stderr separation, noninteractive approval and NDJSON stream behavior.
- TUI: keyboard flows, masked input, resize, back/cancel, plan changes and repeated submission.
- Packaging: source/wheel install, isolated dependencies, sample commands and supported OS behavior.
- Live staging: opt-in, explicit credentials/resources, separately recorded evidence; never part of public pull-request CI by default.

CI must not require provider credentials, contact real deployments, publish packages, or invoke application sends. Provider interaction is mocked at the transport boundary, not by replacing all adapter logic with unconditional success.

## 13. Documentation and definition of done

Required documentation: README, installation and fork workflow, quickstart, manifest/schema reference, dotenv/security contract, provider capability matrix, agent CLI guide, recovery/rollback guide, troubleshooting, testing/qualification records, contribution guide and supported-version policy.

The first useful release is complete when a developer can install locally, configure a generic supported service, generate and understand an immutable plan, explicitly execute through an implemented adapter, inspect a truthful result, and safely recover an uncertain operation. The same flow must work through JSON CLI without an LLM or TUI.

Deployment completion is layered: artifact built, provider accepted, process running, readiness passed, ingress/auth verified, and application qualified. Report each separately. A green health probe is not proof of production readiness.

## 14. Immediate next step

Publish **this plan only** to the requested new repository branch. Do not start implementation or deployments as part of the plan publication. After the plan is accepted or implementation is explicitly resumed, begin Phase 0 and Phase 1 and publish evidence with each completed phase.


## 15. Official architecture references

References checked on **2026-10-23**. These inform the provider-specific architecture and terminal design; they do not demonstrate that an adapter has been implemented, that any application is compatible, or that a live deployment is qualified. Implementation must recheck exact APIs, flags, SDK versions and account-specific behavior.

- [Daytona CLI](https://www.daytona.io/docs/en/tools/cli/): sandbox and snapshot operations are distinct lifecycle actions, not interchangeable with permanent web-service hosting.
- [Railway CLI deployment](https://docs.railway.com/cli/deploying): use explicit project/service/environment targeting and verify deployment outcomes separately from starting an upload/build.
- [Modal web endpoints and ASGI](https://modal.com/docs/guide/webhooks): web workloads require Modal-specific function/application packaging rather than assuming generic container lifecycle parity.
- [Textual workers](https://textual.textualize.io/guide/workers/): long-running operations should not block terminal interaction; cancellation and progress must be coordinated with the execution engine rather than interpreted as automatic remote rollback.
