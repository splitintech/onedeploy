# Configuration and secrets

The normalized configuration describes your source, target, variables and supported runtime profile. It can come from passive discovery plus explicit answers, an optional saved profile, or an existing native manifest. No handwritten Onedeploy YAML is mandatory for supported imports. Use `onedeploy schema --json` for the implementation's authoritative schema. Unknown fields, duplicate YAML keys, custom tags, malformed inputs and unsupported combinations must fail before mutation.

A `.env` file is data, never a shell script. It supports a documented restricted key/value grammar. Do not use command substitution, shell evaluation, automatic expansion or executable `source` statements. Duplicate keys are rejected. Errors should identify a key or line, never repeat the value.

Runtime variables are explicitly declared. Provider account credentials are distinct from runtime variables. The operator must not forward its entire environment into an application. Required application secrets are resolved locally for validation/planning and again at execution, but are not serialized into a plan. Store only references in versioned manifests.

Keep `.env`, plans, deployment journals and connection files private. Plans contain identifiers, paths and operational metadata even though values are redacted. Avoid adding local secret files anywhere a provider CLI might upload. Source bundle hashing binds the actual selected bytes, not just a Git commit label; changing those bytes requires replanning.

Do not trust a manifest merely because it parses. Build files, Python deployment entrypoints and application startup code execute with meaningful permissions. Review them before using Onedeploy. Existing credentials may permit spending money or modifying live services.

Read provider-specific documentation for how runtime variables are delivered. If the provider transport cannot deliver secrets safely, Onedeploy must reject that operation, not put values in process arguments or silently omit them.


## Discovery and optional profiles

`discover` and `resolve` use strict **version 1** local artifacts; `schema --kind discovery|answers|resolution|profile|intent --json` returns their schemas. These are distinct from the version-1 native manifest and the generic JSON envelope. A profile embeds a normalized native manifest, not arbitrary Compose/Railway/application YAML. See [IMPORTERS.md](IMPORTERS.md) for exact mappings and rejected semantics.

- No artifact is saved by `discover` or `resolve` unless `--out` is supplied. The wizard holds discovery/profile/runtime input in memory unless the operator explicitly saves configuration. Necessary deployment journals and recovery/source artifacts are still retained by the core; declining profile export does not disable recovery records.
- An explicitly saved discovery/profile file must be outside the inventoried project or inside its excluded `.onedeploy` directory. Existing files and symlinks are not overwritten. Outputs are exclusive mode-0600 files; newly created parent directories request mode 0700. On POSIX, saved input artifacts, including answers, must not be accessible to group/other (`chmod 600`). Existing parent-directory access is not a complete platform ACL audit.
- Profiles contain absolute discovery/source roots, selected-path metadata, operational target IDs, source inventory, provenance, selected candidate, typed answers and their digest. They are **local source-bound exports, not portable profiles**. The separate [portable intent export/import](PORTABLE_INTENT.md) carries allowlisted reusable settings, not an executable profile or credentials. Moving a project requires fresh discovery/resolution, explicit source trust and new destination bindings; never edit roots or hashes to relocate a profile.
- Original project files stay unchanged. The profile embeds a normalized `source.path: "."` against its explicitly bound root; it does not write a mandatory application manifest. A nested service/build root can be narrower than the original discovery scope, and changes elsewhere in that inventoried scope can still invalidate discovery.
- Provider selection, account/workspace/organization, environment and resource identifiers remain explicit. An imported native target can be preserved but is still revalidated; a profile is neither login state nor permission to deploy. Switching provider requires a new resolution and plan, not credential or target transplantation.
- Profiles carry runtime variable names, types and references, not resolved runtime secrets. Answers cannot supply variable `default` values; use a private env file/explicit environment for runtime values. Existing reviewed native nonsecret defaults are retained as ordinary configuration. Do not put credentials in identifiers, command strings or source. Credential-shaped-literal checks are not a general secret detector.
- A profile includes the original discovery and deterministic resolution. Revalidation checks source inventory and replays the current importer/resolver. Editing a profile's content/hash or moving it does not grant a portable approval. Changed source, configuration, provider or answers require new evidence and a new reviewed plan; execution rechecks profile binding as well as the core's source/remote/runtime bindings.

`plan --profile` writes `.onedeploy/plan.json` relative to the current working directory unless `--out` is supplied. Profile-derived plan saves also refuse overwrite and require an output outside the bound source or inside `.onedeploy`; choose new reviewed filenames for new plans. The legacy manifest plan default/save behavior is unchanged.

To reopen a saved profile in the wizard, enter its JSON path in Step 2 and use **Load saved profile from this path** (the Discover action also recognizes it). The source binding and provider must match. Saved settings are retained, but code trust resets and no plan approval is loaded: review the source and resolve again. Exports default to the selected root’s `.onedeploy/profile.json` and `.onedeploy/plan.json`, and require new filenames when a file already exists.

Use `config validate --profile FILE [--env-file FILE] --json`, `inspect --profile FILE --json` and `plan --profile FILE ...` for the resolved route. Each `--profile` is mutually exclusive with `--manifest`. `inspect` is local configuration/capability inspection, not provider authentication. Only `plan --inspect-remote` opts into provider reads.

### Minimal no-YAML answers example

For a single root Dockerfile with `EXPOSE 8000`, Railway can be resolved using the following answers JSON. These identifiers are examples, not real targets; select and replace them deliberately. When discovery finds multiple inputs/services, also set `candidate` to the exact returned candidate ID.

```json
{
  "name": "demo",
  "profile": "docker-http",
  "target": {
    "account": "owner",
    "project": "project",
    "environment": "staging",
    "service": "web"
  },
  "runtime": {"port": 8000},
  "trust_code": true
}
```

`trust_code: true` is an explicit review assertion, not something an agent should invent to make validation succeed. Without an `EXPOSE` declaration, no port is inferred; supply the actual listener metadata when needed for reviewed checks. Answers may declare supported runtime variables as references such as `"API_TOKEN": {"type": "string", "secret": true, "source": "env:API_TOKEN"}` inside `variables`, never the token value. Compose dependency/storage requirements need complete structured mappings and remain subject to provider capability checks.

The [agent walkthrough](AGENT_CLI.md) covers the full `discover → resolve → plan → approved deploy → observe/reconcile` sequence. Keep all discovery/answer/profile/plan files private and outside upload contexts where practical.

## Conditional values and readiness

Variables may declare `required_if` against a nonsecret controlling variable where supported by the schema. Validation never echoes missing secret values. Keep configuration deterministic and review the resolved variable names before planning.

For Railway/Modal, an optional `runtime.ready_url` is a credential-free HTTPS origin on the provider's official domain. `health_path` and `ready_path` select explicit safe paths. It is bound to the plan and must match the observed target endpoint; no URL is guessed from an app name. Railway can use one unambiguous observed generated domain. Modal requires `target.function` and matching function/app identity; its proxy-authenticated template correctly remains unverified by credential-free HTTP probes. Authentication-required responses are not readiness success, and a public 200 does not prove access control. Custom domains and embedded credentials are not supported by this readiness helper.

For Daytona, readiness uses the reviewed snapshot's Python runtime to perform fixed localhost probes. This does not verify private ingress or application authentication.

## Version 1 field reference

The JSON Schema is authoritative for primitive types, maximum lengths and unknown-field rejection. Provider validation adds narrower requirements. Required top-level fields: `version: 1`, `name`, `provider`, `profile`, `source`, `target`. Names/IDs are nonsecret operational metadata and may appear in plans. Never put credentials in them.

| Field | Contract |
| --- | --- |
| `provider` | `railway`, `daytona`, or `modal`; profile support is provider-specific. |
| `profile` | `docker-http`, `asgi`, `worker`, `model`; inspect capabilities before selecting. |
| `source.path` | Explicit local project directory. No ancestor environment discovery. Included bytes are inventoried; dirty/untracked inputs need explicit opt-in. |
| `source.revision` | Optional expected source revision. It does not replace the actual filtered input-bundle hash. |
| `source.entrypoint` | Reviewed provider-specific entrypoint, notably a Modal Python file. |
| `source.trust_code` | Must explicitly acknowledge executable Modal deployment code; not a sandbox or authorization grant. Docker builds likewise execute reviewed user code. |
| `build.context`, `build.dockerfile` | Local context and Dockerfile selected for a supported provider build. Paths must stay within the approved project bundle. Direct `build.image` input is not a version-1 field; use a reviewed Dockerfile or supported immutable snapshot route. |
| `build.snapshot_name` | Explicit unique Daytona snapshot name for a new project build. Snapshot creation/build precedes sandbox creation and can persist after cancellation. |
| `runtime.command` | Argument array only; accepted only when the provider actually supports the override. Otherwise startup is owned by the reviewed image/Dockerfile/Modal source, and the override is rejected. |
| `runtime.port` | Integer 1–65535. Must match the application's listener and provider-specific packaging. |
| `runtime.health_path`, `ready_path` | Explicit absolute local paths, no arbitrary hosts/query credential transport. Health and model warmup readiness are distinct. |
| `runtime.ready_url` | Optional verified provider-owned HTTPS origin as described above. Never a credential-bearing URL. |
| `runtime.timeout` | Bounded operation timeout, 1–3600 seconds where supported. It is not a spending cap. |
| `target.account` | Exact expected authenticated account/workspace/organization; revalidated immediately before mutation. |
| `target.project/environment/service` | Railway's exact existing project/environment/service identifiers. No implicit linking or creation. |
| `target.mode/sandbox/snapshot/region` | Daytona create/existing mode and explicit names/IDs. Project builds use `build.snapshot_name`; prebuilt paths use a reviewed snapshot. New rollback sandbox names must be approved separately. |
| `target.app/function/environment` | Modal app, optional explicit function for endpoint checks, and environment. Existing authenticated workspace must match. |
| `resources` | Schema admits CPU, memory MB, disk GB and auto-stop minutes; adapters accept only implemented combinations and refuse others. Values are not universal cross-provider units or guarantees. |
| `ingress.mode` | `private` or `existing`; no automatic public exposure or domain/security-policy changes. |
| `rollback.compatible` | Explicit compatibility assertion for a separately planned rollback; does not prove compatibility or undo data/schema effects. |

### Variable contracts

`variables` maps runtime names to specifications. `type` is `string` (default), `integer` or `boolean`. Boolean values are `true`/`false`; integers use decimal digits with an optional minus. `secret` defaults to true; secrets cannot have manifest defaults. Nonsecret `default` values are allowed. `source: env:NAME` selects an explicit local source; omitted source uses the runtime name. Provider-control and process-control environment names cannot be aliased into application values.

Precedence is explicit invocation/TUI overrides, selected process environment, explicitly supplied `.env` file, then nonsecret manifest defaults. `required` defaults true unless a `required_if` condition is supplied. `required_if: {variable: FEATURE_ENABLED, equals: true}` can reference only a distinct declared nonsecret variable; cycles are rejected. Conditions do not execute code.

Dotenv grammar: one `KEY=value` per line; blank lines and comments are allowed. Quotes support explicit escaped newline/carriage-return/tab/backslash/quote characters; physical multiline values, malformed quotes, duplicate keys, NUL and unsupported syntax are rejected. Unquoted inline comments require whitespace before `#`. Expansion-looking strings are literal data, never evaluated or expanded. POSIX private env files require mode 0600. Symlinks are rejected.

### External dependency declarations

`dependencies` is a list of `{name, kind, variable, tls, required}`. `kind` is `postgres`, `redis` or `http`; `variable` references a declared runtime connection value, never embeds a credential in the manifest. TLS declarations validate URL syntax: HTTPS, Redis TLS (`rediss`), or PostgreSQL `sslmode=require|verify-ca|verify-full`. This is configuration validation, not connectivity, certificate-policy or database-schema proof. Use stronger verification modes where required by the application. The tool does not provision databases, migrate schemas or perform application writes.

### Existing storage and model caches

`storage` is a list of `{name, resource_id, mount_path, kind, access, single_writer}` with an adapter-specific `attachment` when required. `kind` is `volume` or `model-cache`; `access` is `read-only` or `read-write`. Mounts must be unique nonoverlapping safe absolute paths. Existing ownership, attachment and provider constraints are inspected before apply; unknown/mismatched resources fail closed.

Railway accepts only its implemented existing-volume attachment modes and validates the service/environment. Daytona currently exposes shared read/write organization volumes on a new sandbox, not an exclusive-writer or read-only guarantee. Requests it cannot honor are rejected. `single_writer: false` is an explicit declaration that shared access is acceptable, not evidence the application's cache is concurrency-safe. Modal storage remains owned by explicitly trusted application code rather than a generic mount translation. No volume deletion or automatic resource takeover is performed.

### Read-only application/authentication checks

`checks` declares at most eight objects with `name`, `path`, and `safety: read-only`. `expected_status` defaults to 200 and must be 2xx. Optional `auth_variable` selects a declared secret runtime value for a Bearer authorization header. Optional `unauthenticated_status: 401|403` requires a negative request without that credential followed by the authenticated request. Both must match before the declared route's ingress check is considered verified.

These checks are GET-only against an endpoint already bound to the observed target/release. No redirects, arbitrary hosts, request bodies, remote hooks, response-body logging, sends or migrations. The operator must ensure the declared GET route really is side-effect-free. Credential transmission to that provider/application must be explicitly authorized through the normal plan/policy boundary. One protected route is not proof of every route's authorization policy or full application qualification. Unsupported provider/check combinations fail before deployment rather than silently skipping checks.

For Daytona, supported explicit application checks run through a fixed localhost HTTP probe in the sandbox using the SDK's scoped process environment. Runtime secrets travel through private stdin/SDK request data, never command arguments. Positive/negative checks can establish `application_auth_verified` for that localhost route; they do not establish public gateway ingress policy, so `ingress_verified` remains false for this probe scope.

For Railway/Modal, `runtime.port` is declared listener metadata, not a provider routing mutation or independently verified port. Railway's reviewed Dockerfile/application must honor its actual provider `PORT` environment; Modal's reviewed web function owns HTTP serving. A plan warning calls out this distinction. Daytona uses the port directly for fixed localhost probes.
