# OneDeploy provider expansion plan

**Updated 2026-11-10. Planning specification, not an implementation or production-support claim.** This adds all 20 requested providers to the roadmap. It complements [AutoDeploy](AUTODEPLOY_PLAN.md), rather than replacing the scoped Daytona GPU rollout. No provider was provisioned, no credentials were created, and no external agent task was submitted during this documentation update.

## Baseline and design decision

OneDeploy already implements a common Python/CLI orchestration layer with Railway, Daytona and Modal adapters. Those integrations are experimental; existence is not live qualification. Preserve their existing contracts and tests. The other 17 entries below are planned integrations, not enabled commands.

Keep wrapping official provider CLIs through typed adapters, using an explicitly selected SDK/API transport only when the needed capability is unavailable through the qualified CLI. Do not concatenate the user's command strings into a shell, reimplement every cloud, silently switch transport, or advertise all providers as interchangeable. Human CLI/TUI and agent JSON interfaces must invoke the same planner, policy checks and executor.

The list mixes five outcomes:

- **Service release:** stable application endpoint, deployment identity and readiness check.
- **Model publication/inference:** model version, prediction schema, hardware and serving policy.
- **Compute job:** job/cluster identity, completion and artifacts; not necessarily a service URL.
- **Sandbox:** isolated execution environment with expiry and cleanup.
- **Development workspace / coding task:** workspace or run identity and reviewable output; not a production release.

Add explicit workload kinds and capability discovery before adding adapters. Unsupported operations must return a typed `unsupported_capability`, not a successful placeholder. A workspace must not claim deployment health, a model upload must not imply a configured dedicated endpoint, and a coding task must never imply permission to merge its output.

## Provider and command review

Commands below are illustrative vendor operations, **not new OneDeploy commands and not a runnable batch**. Bind exact account, project, region and immutable source before execution. Documentation was reviewed on the date above; every adapter must additionally pin and test a concrete CLI version. Where references conflict across product generations, hold that transport until resolved.

| Provider | Status / workload | Requested command and planned treatment | Official reference |
| --- | --- | --- | --- |
| Railway | Existing / service | `railway up` uploads the selected project directory. Reuse adapter; bind service/environment and reviewed source manifest, then wait for deployment readiness. | [CLI](https://docs.railway.com/cli) |
| Fly.io | New / service | `fly launch` creates/configures an app and can deploy. Separate one-time provisioning from repeat `fly deploy`; avoid interactive discovery or optional database creation in unattended execution. | [Launch reference](https://flyio-landing.fly.dev/docs/flyctl/launch/), [essentials](https://flyio-landing.fly.dev/docs/getting-started/essentials/) |
| Vercel | New / web/functions | `vercel --prod` explicitly targets production. Preview is the qualification target; production promotion is a distinct approved operation. Bind team/project and build inputs; don't default agents to `--prod`. | [Deploy CLI](https://vercel.com/docs/cli/deploy) |
| Replicate / Cog | New / model | `cog push r8.im/username/model-name` builds/publishes a model version. Record model/version and schema; distinguish predictions against the version from a dedicated Deployment's hardware/min/max settings. Publishing and inference can incur costs and expose weights. | [Push model](https://replicate.com/docs/guides/build/push-a-model), [Cog CLI](https://cog.run/cli/) |
| SkyPilot | New / compute job, later serving | `sky launch app.yaml` provisions/runs a task on a cluster. Bind cloud/account/region/resources, task YAML and cluster identity. Serving needs a separate capability, not an inferred HTTP endpoint from `launch`. | [CLI](https://docs.skypilot.ai/en/latest/reference/cli.html) |
| Defang | New / Compose service stack | Replace unverified `defang deploy` assumption with documented `defang compose up`. Resolve Playground versus user cloud, stack and deployment mode before planning. Cloud infrastructure/IAM changes need separate visibility. | [Getting started](https://docs.defang.io/docs/intro/getting-started), [deployment](https://docs.defang.io/docs/concepts/deployments) |
| Render | New / service | Replace `render deploy` with `render deploys create <service-id>` for an existing service; pin commit or image and wait. New service creation is a separate plan. This is not a generic upload-local-files command or a promise of Lambda-style functions. | [CLI](https://render.com/docs/cli), [reference](https://render.com/docs/cli-reference) |
| Heroku | New / container service | `heroku container:push web` and `heroku container:release web` are separate journaled steps, not one opaque `&&` shell string. Bind app/process type; a successful push alone is not a release. | [Container registry/runtime](https://devcenter.heroku.com/articles/container-registry-and-runtime) |
| Serverless Framework / AWS | New / functions and infrastructure | `serverless deploy` can update an entire infrastructure stack. Bind AWS identity/region/stage and reviewed generated template; surface IAM, triggers, storage and deletion effects. Do not mislabel a stack rollback as database recovery. | [Deploy reference](https://www.serverless.com/framework/docs/providers/aws/cli-reference/deploy) |
| Google Cloud Run | New / service | `gcloud run deploy --source .` needs explicit service/project/region and invokes a source build. Record Cloud Build and revision identities; public access/IAM is a separate explicit setting, never a default convenience flag. | [Source deployment](https://docs.cloud.google.com/run/docs/deploying-source-code) |
| Aether | New, identity/command qualification hold / coding task | Preserve requested `aether run "..." --size medium` as intent, not a verified adapter command. Several products use this name. A candidate official source at onaether.dev documents server/workspace/agent setup and run branches; the exact product and `--size medium` contract are not established. Do not install, authenticate or send code/prompts until identity and noninteractive lifecycle are confirmed. | [Candidate quickstart](https://onaether.dev/docs/quickstart) |
| Daytona | Existing / sandbox, scoped GPU AutoDeploy | Requested `daytona sandbox create --image py312` is generation-sensitive. Existing project transport and pinned SDK remain authoritative; current references differ in image/snapshot vocabulary. `py312` alone neither pins an image nor requests a GPU. Add GPU lifecycle through the existing explicit AutoDeploy adapter, not a duplicate provider. | [CLI](https://www.daytona.io/docs/tools/cli), [sandboxes](https://www.daytona.io/docs/sandboxes) |
| Modal | Existing / Python deployment, expanded GPU later | `modal deploy my_script.py` deploys a Modal app. Reuse current adapter; qualify new GPU/model profiles separately from existing ASGI support. Python import/build code is executable and must be reviewed. | [Deploy CLI](https://modal.com/docs/cli/latest/deploy) |
| E2B | New / sandbox | `e2b sandbox create base` is a sandbox operation, not hosting. Pin template identity, timeout and CLI/API generation. Prefer noninteractive detached creation where the qualified CLI supports it; collect ID, inspect, execute only authorized work, then clean up. | [Official repository example](https://github.com/e2b-dev/E2B/blob/main/skills/stripe-projects/SKILL.md), [product updates](https://e2b.dev/product-updates) |
| Coder | New / workspace | `coder create --template="template-name" workspace-name` requires a Coder deployment, organization/template version and parameters. Workspace readiness is not application deployment readiness. Show the infrastructure effects of its template. | [Create reference](https://coder.com/docs/reference/cli/create) |
| DevPod | New / workspace | `devpod up https://github.com` is missing a concrete repository. Require owner/repo and selected backend/context; pin devcontainer/source revision. Review lifecycle hooks and credential forwarding; never silently reuse all local credentials. | [Create workspace](https://devpod.sh/docs/developing-in-workspaces/create-a-workspace) |
| Gitpod / Ona | New, generation qualification hold / workspace | `gitpod workspace create <repo-url>` is documented for Gitpod Classic. Current Ona tooling must be a separately versioned contract; do not conflate the workspace-local `gp` utility or blindly rename executable/flags. Require concrete repo URL and product generation. | [Classic CLI](https://ona.com/docs/classic/user/references/gitpod-cli), [official setup action](https://github.com/gitpod-io/setup-ona) |
| Cloudflare | New / Worker/web | `wrangler deploy` targets Workers with selected account/environment/bindings. Pages and other products are separate capabilities. Bind compatibility date, routes and resource bindings; preview/production routes and secret updates are explicit. | [Wrangler commands](https://developers.cloudflare.com/workers/wrangler/commands/) |
| Deno Deploy | New / web service | Do not base a new adapter on `deployctl deploy`: official docs mark Deploy Classic/deployctl sunset, with July 20, 2026 listed for Classic shutdown. Target current `deno deploy` contract after version qualification; legacy migration is separate. Do not assume arbitrary Docker image deployment. | [Migration](https://docs.deno.com/deploy/migration_guide/), [current CLI](https://docs.deno.com/runtime/reference/cli/deploy/) |
| Koyeb | New / container service | `koyeb app init my-app --docker my-image --ports 8000:http` expresses app+service creation from an image, not local source upload. Current reference uses `apps init` and singular `--port` in service examples; pin/test accepted aliases and flags rather than assuming plural forms. Prefer immutable registry digest and explicit route/region/instance. | [CLI reference](https://www.koyeb.com/docs/build-and-deploy/cli/reference) |

## Provider-agnostic GitHub AutoDeploy

**Additional product requirement:** AutoDeploy is a vendor-agnostic Railway-like experience: connect a repository, select a deployment branch and target, then a trusted push to that branch triggers the configured release without requiring a person to run vendor CLI commands each time. A reviewed merge is the normal way such a push reaches a protected production branch, but the trigger is a branch push, not a PR-number heuristic. Daytona is the first specialized GPU implementation, not the product boundary.

This applies to every **qualified deployment capability** whose provider lacks an equivalent native integration. The 20-name roadmap is not a claim that every vendor is currently supported or that a developer workspace is a production hosting service. Qualification holds stay explicit. Native integrations and custom orchestration must provide one coherent experience, not two independently competing deploy triggers.

### Detect, select and persist one deployment owner

Discovery records native Git integration capability as `supported`, `unsupported` or `unverified`, along with account/product generation, reference/version and read-only observed configuration. Check the actual target, not only whether the vendor markets Git integration. Inspect linked repository and branch, trigger filters, build configuration, checks/approval gates, commit pinning, deployment status, promotion/rollback and cancellation semantics. Redact tokens/webhook secrets. Lack of permissions to inspect is `unverified`, not proof that the feature is absent.

Choose and save exactly one mode per target:

1. **Native passthrough:** use an existing native GitHub→branch→build/release integration if it satisfies the requested policy. OneDeploy normalizes setup instructions, status and operation records; it does not run a second deployment on the same push. Do not claim to enforce a test gate or promotion policy that the native provider cannot actually enforce. Configuring a new native integration or changing its branch/settings is a separately reviewed write.
2. **OneDeploy-managed:** for a qualified provider without a suitable native equivalent, generate a reusable GitHub Actions pipeline and provider adapter configuration. GitHub invokes OneDeploy, and OneDeploy calls the official qualified transport. Operators do not have to rerun local CLIs on subsequent pushes.
3. **Blocked/unverified:** expose missing capability or permission and a concrete qualification task. Do not install another tool, disable existing hooks or fall back from native to custom deployment silently.

A native integration that lacks a required test gate, exact-commit binding or rollout requirement is not equivalent merely because it can deploy from Git. The plan may recommend managed mode with the native trigger disabled, but activation must atomically establish the selected owner as far as provider APIs allow, or remain paused until an operator verifies that only one trigger is active. Migrating modes preserves the last known good release and history. The discovery UI/JSON reports the chosen mode, reason, gaps and evidence date.

### Shared push-to-release contract

The proposed library, CLI and configuration accept repository identity, allowed branch, path filters for monorepos, target account/project/environment, workload profile, versioned build recipe, qualification commands, secret references, release policy and cost/retention ceilings. A versioned YAML file and Dockerfile (when relevant) live in the application repository. Expose provider-specific options without turning the YAML into an arbitrary privileged shell dispatcher.

For managed mode:

```text
trusted push to configured GitHub branch
  → verify source repo/ref/SHA and policy
  → run exact-commit tests and qualification prerequisites
  → build/publish immutable artifact, or invoke qualified provider build
  → create candidate release/resource using vendor-specific adapter
  → inspect actual completion and health/inference outcome
  → promote if this workload/provider supports a separate promotion
  → drain/retire previous compute if applicable and authorized
  → publish commit-bound status and durable deployment record
```

Not every provider needs Docker, a registry, snapshots, stable workers or a separate traffic switch. Serverless code bundles, Git-triggered builds and model versions are valid immutable release artifacts. An adapter declares which stages are native, OneDeploy-managed, inapplicable or unavailable. Reject a requested staged rollout if the provider only supports an immediate replacement and that does not meet policy; never fabricate a candidate or drain success. For the specialized Daytona GPU profile, the artifact→snapshot→candidate→promote→drain sequence remains unchanged.

Generate workflows only for selected, qualified profiles. Use least-privilege GitHub permissions, pinned actions, trusted exact-SHA checkout, reviewed environment gates and scoped credentials/federation where available. Fork PRs run offline tests without deployment secrets. A push event does not authorize new credentials, changed security settings, arbitrary paid resources or broader data sharing. Initial activation must bind standing release authorization to repo/branch/target/material limits. Changes outside that scope fail closed rather than become automatic approval.

Use a durable deployment ledger and target lease in addition to Actions concurrency. The idempotency key includes repository, exact commit, target and configuration generation; repeated delivery reconciles the same operation. Explicitly define latest-wins versus deploy-every-commit policy. Canceling an older workflow must not leave an unrecorded paid candidate or interrupt production drain blindly. Prevent an old build that finishes later from promoting over the latest permitted generation. Pin source/image/model identities through every provider build and verify the deployed revision, rather than reporting success when a CLI merely accepted a request.

Status must distinguish queued, testing, building, candidate, verifying, promoting, draining, complete, failed, superseded and needs-reconciliation, with provider stages marked inapplicable when appropriate. Human UI, agent JSON and Python API use the same records. Include the exact deployed SHA, artifact/provider revision, endpoint if applicable, mode, test evidence, rollback scope and unresolved cleanup. No service URL is invented for a job or workspace.

### Workload boundaries and vendor coverage

- **Service platforms:** qualify a native Git integration first; otherwise implement managed releases using the provider matrix. Native Git availability is target-specific and must be verified before choosing a mode. Vercel/Render/Koyeb-style Git workflows must not receive a duplicate Actions deployment merely to advertise OneDeploy coverage.
- **Model platforms:** trigger a version build/publication and synthetic inference test; update a dedicated deployment only if that distinct capability is configured and qualified. A successful model upload alone does not certify the production endpoint.
- **Infrastructure/compute tools:** a branch push can initiate a reviewed stack/job workflow when explicitly configured, but resource changes, job completion and cleanup are the relevant acceptance checks. They are not automatically a web-app rollout.
- **Sandboxes/workspaces/coding tasks:** the same event framework can offer explicitly named template-refresh, preview-workspace or task-run workflows later. It must not brand those outcomes as production deployment or automatically run an external coding agent. Only a separately qualified serving profile participates in the production-release pipeline.

Every listed vendor gets a native-capability research row before implementation: product/account generation, inspected target, integration availability, checks/branch configuration, chosen mode, qualified artifact/workload and missing gates. Initially unknown entries remain unknown; this document does not assert a researched native-support verdict for all 20 vendors.

### Additional implementation and acceptance work

Build provider-neutral trigger/configuration and release-driver interfaces around the existing lifecycle engine. Keep Daytona details in its adapter. Add native-integration inspection/status adapters, workflow generation/validation, mode-transition preflight, normalized deployment records and a generated coverage matrix. Separate `native_passthrough`, `managed_release` and workload automation capabilities. No new vendor is selectable for live apply until its gates pass.

Tests must cover duplicate native+managed trigger detection, source/ref spoofing, path filters, denied native inspection, unsupported native test gates, target/branch drift, repeated webhook delivery, concurrent commits, stale promotion, partial native configuration, cancellation after create, secretless fork CI, failed build/health check, no-promote providers, rollback limits and owned-resource cleanup. Qualify one end-to-end native passthrough target and one managed service target, then the Daytona GPU profile, with explicit spend limits. Re-run the same contract suite per new adapter; do not infer 20-provider compatibility from one successful Daytona demo.

The target outcome is one install/setup experience and repeatable branch-driven releases for all supported deployment profiles. Shipping status is reported per provider, mode, workload and version. A documentation update, generated YAML, mocked CLI invocation or merged PR is not evidence that automatic deployment has been enabled in an account.

## Common adapter contract

Extend existing provider interfaces rather than a second command registry. Each provider manifest declares:

- Provider ID, product generation, official distribution URL, supported/pinned version range and explicit CLI/SDK/API transport.
- Workload kinds; discover/plan/apply/inspect/logs/cancel/cleanup support; build, publish, promote, rollback, GPU, volume and scale-to-zero capabilities independently.
- Input types: source directory allowlist, exact Git SHA, Docker digest, model revision, Compose/task/template identity. Reject generic `github.com` roots and unresolved mutable references at apply.
- Target identity tuple: organization/account/project, service/workspace/cluster, environment, region. Never use ambient CLI context without comparing it with the approved plan.
- Authentication requirements and least-privilege scopes; noninteractive support, expected command output format, timeout and cancellation behavior.
- Returned artifact/resource identities, observed lifecycle state, readiness probe, cleanup ownership and qualified rollback scope.

Use a capability enum with `supported`, `unsupported`, `unverified`, rather than one `supports_deploy` boolean. Discovery may identify a project without executing its build hooks. Schema changes must preserve existing plans or fail with a migration instruction; no silent defaulting to production.

### Human and agent parity

The human wizard shows capability, cost and target differences. Agents receive the same information in versioned JSON with stable exit codes. Both require a source-bound reviewed plan before writes. Noninteractive mode fails rather than opening a login/browser, selecting a default team, accepting terms, or answering new provider prompts automatically. Return actionable missing prerequisites without leaking secrets. Provide equivalent Python API examples and CLI reference; task/workspace operations get explicit names rather than overloading every action as `deploy`.

### Execution and recovery

Execute argv arrays with `shell=False`; no free-form command forwarding in the safe interface. Resolve trusted executable paths and enforce the qualified version. Keep vendor stderr/stdout bounded and redacted. Provider output is untrusted data, not new instructions.

Journal each externally visible step before/after execution with operation ID and resource IDs. A timed-out create is an **unknown outcome**: inspect/reconcile before retrying. Two concurrent controllers must not provision or promote the same target twice. Use per-target leases and expected-generation/CAS promotion. Cancellation stops new work; clean up only resources proved owned by this run and only within the cleanup policy. Never delete a user's pre-existing workspace or retained volume to make a test pass.

Rollback describes what it restores: application version/traffic, not arbitrary data, billing, IAM or model publication. Where no safe rollback exists, mark it unsupported and require a recovery procedure. Persist old release identity before promotion; drain active requests/jobs before retiring old compute.

## Authentication, data and cost controls

Use existing trusted credential mechanisms, short-lived federation where supported, and secret references rather than values in plans. Never print credentials, serialize them into artifacts, place them in command-line arguments when a safer supported channel exists, or copy a user's entire CLI home into a worker. Account creation, new access grants and credential persistence are separate actions.

A plan lists uploaded source/model/data, destination, public/private exposure, ingress and egress policy, build hooks, IAM changes, resources and retention. Exclude `.env`, VCS metadata, tokens and unrelated local files from context archives. Explicitly review Docker/Compose/devcontainer/model/task hooks before running them. The agent-task path must restrict repository/branch access, budget, allowed tools and output permissions; dispatch is not implicit approval to publish or merge.

Require a user-approved spend envelope before paid qualification or deployment: provider, resource shape/count, region, maximum runtime, persistent storage, idle retention, build/registry/egress costs and cleanup. A rough price estimate is not a hard cap. If provider hard limits are unavailable, disclose that fact and use controller limits plus reconciliation alerts without promising a billing guarantee. Zero replicas can still leave storage/registry/build costs. No subscription, production ingress, paid database or GPU upgrade is an inferred default.

## Delivery phases and acceptance gates

1. **Capability foundation:** versioned manifest/schema, identity checks, shared runner, typed results/errors and contract fixtures. Preserve Railway/Daytona/Modal tests and public API. List planned providers as planned, not selectable production backends.
2. **Web/service cohort:** Fly.io, Vercel, Render, Heroku, Cloud Run, Cloudflare, Koyeb. Start with existing explicitly selected resources, then separately qualify provisioning. Add current Deno only after generation verification.
3. **Infrastructure/model cohort:** Defang, Serverless/AWS, Replicate and SkyPilot. Add artifact/version and stack/job semantics, model licensing/weights, cloud-policy review and cost controls. Expand Modal GPU using its existing provider.
4. **Sandbox/workspace cohort:** E2B, Coder and DevPod; reuse Daytona. Gitpod/Ona waits for generation qualification; Aether waits for identity and command contract. Unblocked cohorts proceed without these holds.
5. **Provider-agnostic branch AutoDeploy:** ship native-integration discovery/passthrough and the managed GitHub push-to-release contract above for each qualified deployment capability. Implement provider-specific release mechanisms and exactly one active trigger owner. Daytona snapshot creation is not imposed on every vendor. Workspaces/jobs are excluded from service promotion unless they genuinely expose a qualified serving adapter.

Each adapter progresses through `planned → offline-tested → staging-qualified → supported` **per capability and version**, not per brand. Stage gates:

- Golden argv/output fixtures for the pinned CLI; missing/expired auth, wrong account/region, unsupported flags/version and output-schema drift fail closed.
- Injection, path traversal, secrets-in-logs and accidental context-upload tests; no execution of discovered project code during read-only inspection.
- Unit and contract tests for success, failed build, no capacity, timeout after creation, duplicate apply, quota/rate-limit, cancellation and reconciliation after restart.
- Tests for human/JSON/Python parity, preview-versus-production targeting, CAS races, failed promotion, safe drain and retained-data protection.
- Opt-in isolated live qualification, synthetic input only, explicitly approved spend/permissions. Verify actual resource IDs, health or job completion, logs, rollback where supported and post-test cleanup/cost reconciliation.
- Documentation records exact source SHA, provider/CLI version, test date, account class/region, passed/failed/not-run capabilities and known limits. Never convert mocked success into live qualification.

Proposed implementation additions: extend provider manifests and schemas; provider-specific modules under the existing provider package; shared contract tests plus fixture directories; one minimal example per workload kind; a generated capabilities table and troubleshooting/credential prerequisite pages. Keep the minimal installation lightweight through optional provider dependencies.

## Completion definition

This request updates the roadmap for every listed vendor. Implementation is complete only when the corresponding capability passes the gates above and is published with truthful support labels. Updating this document alone does not enable those providers. The ongoing Daytona Whisper work remains scoped to inference only; the Railway voice API and post-processing are not migrated by this expansion.
