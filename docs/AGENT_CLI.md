# Agent-facing CLI contract

A local agent uses the same Onedeploy executable, discovery/resolution model, provider adapters and execution engine as the human interface. No LLM account, agent-specific SDK, built-in MCP server or privileged deployment service is required. Invoke commands as argument arrays, not interpolated shell strings.

**Experimental 0.2.0a1 increment:** `discover`, `resolve`, `export`, `import`, `config validate --profile`, `inspect --profile`, `plan --profile` and `tui --wizard` are additions. Manifest-based commands remain available. Read [the exact importer subsets](IMPORTERS.md); a recognized config is not a complete deployment specification or proof of provider compatibility.

## JSON-only existing-project journey

The following commands are a sequence to review, not a script that grants deployment permission. Choose an existing project with a supported input and keep operational artifacts in a private directory **outside that project's source scope**. Substitute real nonsecret target identifiers only after the user selects the destination. Do not substitute tokens, passwords or secret values.

```sh
work="$(mktemp -d)"
chmod 700 "$work"
onedeploy schema --kind discovery --json
onedeploy schema --kind answers --json
onedeploy discover --provider railway --path /absolute/path/to/project \
  --out "$work/discovery.json" --json
```

1. Parse the success envelope's `result`, not console text. Discovery can succeed while `result.status` reports unresolved inputs. Review candidate IDs, missing fields, conflicts and unsupported constructs in the artifact, plus the envelope’s `next_actions` guidance.
2. Select the intended candidate/service and exact provider target. Create a JSON answers object that conforms to `schema --kind answers`, and set the file to mode 0600 on POSIX (`chmod 600 "$work/answers.json"`). Put only typed nonsecret configuration and secret **references** in it. Use the [minimal Dockerfile/Railway answers example](CONFIGURATION.md#minimal-no-yaml-answers-example) as a shape reference, not as target authorization. Never paste embedded config values, `.env` contents or raw provider output into an agent prompt.
3. Resolve and inspect the resulting normalized profile/provenance:

```sh
onedeploy resolve --discovery "$work/discovery.json" \
  --answers "$work/answers.json" --out "$work/profile.json" --json
onedeploy inspect --profile "$work/profile.json" --json
onedeploy config validate --profile "$work/profile.json" \
  --env-file /private/application.env --json
onedeploy plan --profile "$work/profile.json" \
  --env-file /private/application.env --out "$work/preview.json" --json
```

Omit `--env-file` when no application runtime variables require it. A local preview does not contact the provider and cannot execute. By default the source must be a clean Git checkout. `--allow-dirty` remains an explicit opt-in for uncommitted/untracked or non-Git source; do not add it automatically to suppress source-review requirements.

4. After destination/code review, explicitly allow authenticated read-only inspection:

```sh
onedeploy doctor --provider railway --json
onedeploy plan --profile "$work/profile.json" \
  --env-file /private/application.env --inspect-remote \
  --out "$work/plan.json" --json
```

`doctor` checks installed tools and versions, **not** authenticated account ownership. `plan --inspect-remote` uses existing local provider authentication and reads the exact identity/target. It is not a login, token-creation or permission-grant command. Do not treat account metadata or a remembered profile as the user's choice of destination.

5. Present the exact plan and obtain authorization through the embedding system's trusted user/policy boundary. Review source and target identity, operations, variable recipients, ingress/storage/lifecycle effects and cost uncertainty. Only after that authorization:

```sh
onedeploy deploy --plan "$work/plan.json" --approve-plan REVIEWED_PLAN_ID \
  --env-file /private/application.env --json
```

This is a **real mutation**, not a simulation mode. It can create resources, transmit selected runtime values and incur charges. There is no global `--yes` authorization bypass. Offline tests simulate transport; the production CLI does not silently substitute a fake deployment.

6. Observe the returned deployment ID and layered verification. Reconcile any uncertainty instead of retrying a write:

```sh
onedeploy status --deployment DEPLOYMENT_ID --json
onedeploy logs --deployment DEPLOYMENT_ID --follow --max-polls 12 --json
onedeploy resume --deployment DEPLOYMENT_ID \
  --plan-out "$work/recovery-plan.json" --json
```

`resume --plan-out` writes only when a real safe recovery plan is available. Recovery/rollback plans require a new review and approval. See [recovery](RECOVERY.md) before operator assertions or rollback. Use a consistent `--state-dir` on commands that support it when overriding the default private state directory.

`discover` and `resolve` never prompt, and neither writes an artifact without explicit `--out`. Their stdout envelope is not the same as the bare saved artifact: use `--out` or extract and validate the documented `result` object rather than passing the whole envelope back as a discovery/profile. Existing discovery/profile output files are not overwritten. `plan --profile` defaults to `.onedeploy/plan.json` relative to the current working directory when `--out` is omitted; explicit profile-plan outputs must be outside the bound project or under `.onedeploy`, and are also exclusive/no-overwrite. The legacy manifest plan default remains `plan.json`. There is no mandatory `onedeploy.yaml` generated in the project.

## Reuse configuration on another checkout or machine

A local profile cannot be relocated by rewriting paths/hashes. [Portable intent](PORTABLE_INTENT.md) is the separate non-executable route:

```sh
onedeploy export --profile "$work/profile.json" --out "$work/intent.json" --json
onedeploy import --intent "$work/intent.json" --path /absolute/path/to/new-project \
  --answers /private/fresh-answers.json --out /private/new-profile.json --json
```

Export/import do not save without explicit `--out`. Intent contains no old source identity, trust, target/resource IDs, runtime values or approval. Import always discovers the new project and requires fresh code trust and exact destination confirmations; omitted answers return typed questions rather than prompting. New answers can replace reusable settings but cannot mask conflicting explicit source settings. Only the newly resolved local profile can enter the usual validation/planning/authorization flow. The source must be copied/reviewed separately; these commands do not transfer code or credentials.

## Stable envelopes and typed blocked outcomes

Ordinary commands emit one final JSON envelope on stdout:

- `schema_version` is `"1"` for the generic envelope.
- `command`, `status`, `result`, `error`, `warnings` and `next_actions` are always present.
- A success has `error: null`. A failure has `result: null` and the stable error fields `code`, `message`, `retryable`, `uncertain`, `mutation_possible`.
- Discovery returns an artifact with its own version/status. A discovery success means inspection completed, not that resolution or deployment is possible.
- Blocked resolution uses a typed `input_required`, `conflict` or `unsupported_capability` error. When available, `error.details` contains the schema-validated resolution outcome: `status`, `missing`, `conflicts`, `unsupported`. It never turns a failed operation into a successful `result`.
- Do not copy arbitrary error text into an answer. Read the question identifiers/types and obtain the required choice from the trusted user/context. Unsupported requirements cannot be accepted by ignoring a warning.

Progress events go to stderr, with a sequence, timestamp and allowlisted operational IDs. Parser errors are value-free. Do not scrape stderr for successful results or authorization. `schema --kind manifest|discovery|answers|resolution|profile|intent|result|progress|error --json` returns the corresponding contract inside the ordinary result envelope. Retain versions in clients; the generic result schema is not a substitute for command-specific artifact schemas.

### Exit statuses

| Exit | Stable code | Meaning / safe next step |
| --- | --- | --- |
| 0 | success | Inspect command-specific status and verification; not proof of successful deployment. |
| 1 | `internal_error` | Stop and report a sanitized reproducible error. |
| 2 | `invalid_config` | Fix the rejected input or arguments. |
| 3 | `unsupported` | Existing core/adapter operation is unsupported. |
| 4 | `approval_required` | Obtain trusted authorization for the exact reviewed plan. |
| 5 | `authentication_failed` | Resolve the local authentication/identity problem deliberately; no auto-login. |
| 6 | `provider_failed` | Inspect journal and mutation flags; do not assume no side effects. |
| 7 | `uncertain` | Inspect/reconcile the recorded operation. Never blindly replay. |
| 8 | `cancelled` | Local cancellation, not cloud deletion/rollback. |
| 9 | `stale_plan` | Review changed input/evidence and generate a fresh plan. |
| 10 | `locked` | Resolve the active local/same-target operation; do not bypass it. |
| 11 | `input_required` | Answer the typed missing questions, then resolve again. |
| 12 | `conflict` | Make the explicit candidate/service/configuration choice. |
| 13 | `unsupported_capability` | Change to supported packaging/requirements; never silently drop behavior. |

Always inspect `uncertain` and `mutation_possible`, even when the code suggests a retryable failure. A nonzero exit is not proof that a provider rejected a write. The local CLI cannot enforce every external agent platform's policy: **reading or supplying a plan ID is not proof of human authorization**.

## Persistence, provenance and invalidation

Use an in-memory result or an explicit private artifact file. [Configuration](CONFIGURATION.md#discovery-and-optional-profiles) defines source/path binding, privacy and migration. Discovery/resolve preserve source files. Resolution must not silently choose a Compose service, transplant provider identifiers or erase a storage/dependency/authentication requirement.

Profiles, provenance and plans contain operational identifiers and local paths even when secret values are absent. Keep them private and outside upload contexts. Runtime application values come from explicitly selected environment sources/private files or masked interactive input, not answers JSON or CLI flags. Provider authentication stays separate from application runtime variables. Do not capture or forward entire environments.

Material input/source/provider/answer changes require re-discovery/resolution or replanning as applicable and fresh approval. A profile hash binds a reviewed resolution, not trust in the code that later builds or runs. Docker build instructions and Modal deployment source execute only in the later authorized deployment path, not during discovery.

## Observation is not application qualification

A successful status/reconciliation command only means the observation completed. Inspect `local_state`, provider state and verification layers. `provider_accepted`, `process_running`, `readiness_passed`, `application_checks_passed`, `ingress_verified` and `application_qualified` are distinct. One declared protected route passing negative and authenticated checks does not prove every route's access control or production suitability.

`logs --follow --json` emits newline-delimited result envelopes for changed bounded snapshots and a terminal result. Identical consecutive snapshots are suppressed; changed snapshots can overlap. It is not a lossless incremental stream. Ctrl-C ends polling with exit 8 and does not stop the cloud deployment. Logs can contain private application data despite best-effort redaction; request them deliberately and never forward them automatically.


## Installed provider authentication and Daytona transports

Use the same operating-system user/configuration context as the already authenticated official CLI. `doctor` proves local availability/version only, never login. Remote inspection verifies target identity before deployment. Onedeploy does not import credentials from another agent or computer, install tools automatically, or initiate login.

Daytona defaults to official CLI 0.226.0 with browser-session reuse. Profiles explicitly choosing `target.transport: sdk` use the optional SDK 0.226.0 and its separate API key; there is no automatic fallback. Agent resolution must answer any transport question explicitly. Switching transport invalidates prior plans and requires a fresh source-bound plan and approval.

CLI runtime variables must be explicitly `secret: false`, and their values become visible in the local provider process arguments. Plans disclose that exposure without persisting the values. Sensitive or unspecified-secrecy variables, authenticated probes and logs require explicit SDK transport. Never change a variable's secrecy classification merely to make validation pass. A provider API-key-only CLI profile cannot satisfy the browser active-organization proof; return the typed actionable failure instead of trying another auth route automatically.
