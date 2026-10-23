# Passive project discovery and importer contract

This is the implemented **experimental version-1 subset**, not the broader proposed matrix in `Onedeploy.md`. The source of truth is `onedeploy/discovery.py`, its schema functions and importer tests. Importer acceptance and provider capability validation are separate gates. [Acceptance evidence](UX_ACCEPTANCE.md) records tests and unrun qualification rather than assuming a recognized file is deployable.

## Selection and preservation

`discover --provider PROVIDER --path PATH --json` accepts a local project directory or an explicit file. Directory discovery recognizes `onedeploy.yaml`, `onedeploy.yml`, `Dockerfile`, the four `compose.yaml`/`compose.yml`/`docker-compose.yaml`/`docker-compose.yml` names, and `railway.toml`/`railway.json`. For Modal it considers Python files with a literal `import modal` or `from modal` marker; the latter can be recognized and then rejected by the narrower static importer. An explicitly selected native YAML/JSON manifest can have any filename. An explicit Python file is attempted as a Modal entrypoint.

Multiple recognized inputs or Compose services require an explicit candidate ID. No first service or preferred file wins silently. Select the intended entrypoint/configuration deliberately: the tool does not merge the semantics of every file in a repository. Conflicting explicit fields in a selected candidate cannot be masked by answers; correct the source or choose another supported source and discover again.

Discovery never writes or executes the application, calls a provider, installs a package, invokes a shell/Git, imports application Python, builds an image or fetches a schema URL. It inventories and reads local bytes only. It does not auto-load `.env`. Original Dockerfiles, YAML/JSON/TOML and Python remain unchanged. A later deployment can execute reviewed build/startup code, including network access by that code; passive discovery is not a sandbox or code-safety certification.

## Exact mappings and rejections

### Native Onedeploy manifest

- Accepts strict manifest **version 1**, in YAML or JSON, after full manifest validation. Unknown keys, unsupported types, duplicate keys, unsafe YAML tags/aliases and schema violations fail.
- Preserves declared fields. Resolves `source.path` relative to the original manifest, binds the resulting project root and normalizes the stored `source.path` to `.`. The source must remain within the selected discovery scope.
- An explicitly different `provider` is a conflict, not an automatic conversion. Adapter validation still rejects incompatible profiles, targets and operations.
- Obvious literal credentials in defaults/command arguments/URLs are rejected from imported evidence. This is a bounded heuristic, not proof that arbitrary source contains no secret. Existing nonsecret manifest defaults remain ordinary configuration; answers files do not admit defaults.
- The legacy `--manifest` route remains compatible and does not require discovery.

### Dockerfile project: Railway or Daytona

| Input | Normalized behavior |
| --- | --- |
| Root `Dockerfile` in the selected build context | Preserve original instructions; `build.context: "."`, `build.dockerfile: "Dockerfile"`; suggest `profile: docker-http`. |
| Static `EXPOSE N` or `EXPOSE N/tcp` | Offer integer listener candidates. The user must confirm `runtime.port`; metadata is not proof of a listening HTTP service. |
| Dynamic or non-TCP `EXPOSE` | Require an explicit actual listener answer; no automatic port execution/inference. |
| Credential-shaped `ENV`/`ARG` default | Block import; remove the embedded credential and use runtime references. |
| Source/build trust | Require explicit `trust_code: true` for imported non-native packaging. |

The scanner requires a `FROM` instruction but does not validate or evaluate the full Dockerfile language, resolve base-image digests, read runtime values or build the image. No port is invented when none is declared. `Dockerfile.*` can be recognized explicitly, but the current container adapters require a file named `Dockerfile` at the selected build root. A selected nested context is rebased; arbitrary custom adapter build roots are not promised. Modal does not accept this container transport. Additional runtime variables/dependencies/storage cannot be inferred reliably from build instructions and require explicit configuration.

### Compose: one selected build-backed service

Accepted syntax is a static subset of the Compose structure: absent `version`, or `3`/`3.0` through `3.9`. This is not full Compose Spec or engine conformance. Top-level allowed keys are `name`, `version`, `services`, `volumes`; top-level volume entries must be empty objects or null. Project `name` is not automatically adopted as the deployment name.

| Selected service field | Accepted mapping |
| --- | --- |
| `build: ./context` or `build: {context: ./context, dockerfile: Dockerfile}` | Local safe build context and original Dockerfile. Rebase the selected source root; no remote build or extra build settings. |
| `environment: [NAME, OTHER_NAME]` or `{NAME: null}` | Names become required secret string references `env:NAME`. No values are collected or expanded. |
| `ports`, `expose` arrays | Static single TCP container ports become choices for `runtime.port`. Supported short forms include `8000`, `8080:8000` and `/tcp`. Explicit IP/host bindings, including `127.0.0.1:` and `0.0.0.0:`, are rejected because their exposure restrictions cannot be dropped. Host publication is not reproduced or authorized. When no Compose port is declared, Dockerfile EXPOSE questions are retained. |
| `depends_on: [db, cache]` | Every listed name must be mapped exactly once to an explicit external dependency with kind, variable, TLS and required policy. No dependent service is created or started. |
| `volumes: [cache:/cache:rw]` | Every named mount must map to an existing supported provider storage resource, retaining its name, mount path and access. Default access is read-write; `:ro` requests read-only and may be rejected by the adapter. |

All other selected-service keys block this importer. This includes image-only services, `command`/`entrypoint`, `env_file`, literal environment values (even nonsecret ones), interpolation, `include`/`extends`, extra build arguments/secrets/platforms, networking, privileges, devices, resource/lifecycle settings, conditional dependencies, long port/mount syntax and bind mounts. No unsupported behavior is silently discarded.

Only the selected service is deployed. Other services are neither orchestrated nor converted to managed databases. A mapped dependency is an external runtime contract, not provisioning or connectivity proof. Provider storage limits remain binding: for example, generic read-only/exclusive-writer guarantees are not available on every adapter. Compose does not import into Modal.

### Railway TOML/JSON: static Dockerfile subset

| Input | Mapping / limit |
| --- | --- |
| Top-level `build`, `deploy`, optional `$schema` | Other sections are unsupported. The only accepted explicit schema reference is `https://railway.com/railway.schema.json`; it is never fetched. |
| `build.builder: "DOCKERFILE"` | Only this builder is supported. If absent, the importer selects the Dockerfile path, not Nixpacks/Railpack. |
| `build.dockerfilePath` | Defaults to `Dockerfile`; must resolve safely and satisfy the adapter's root-Dockerfile restriction. |
| `deploy.healthcheckPath` | `runtime.health_path`; original provider config stays in source. |
| `deploy.healthcheckTimeout` | `runtime.timeout`, also used as bounded local operation/verification metadata. This is not a semantic equivalence guarantee for every provider timeout. |

All other build/deploy keys are rejected, including start/build commands, replicas, resources, restart policy and other lifecycle settings. Destination IDs and authentication are never inferred. The original Railway config remains authoritative code/configuration in the reviewed upload; this importer does not rewrite it. Only Railway is supported for this input. For a Railway destination, a candidate whose effective source root contains `railway.toml` or `railway.json` must select that configuration importer explicitly, including when another candidate is a Dockerfile or native manifest. This prevents bypassing settings the provider could implicitly consume. Both Railway config files present at once is blocking ambiguity, not a guessed precedence.

### Modal Python: bounded literal ASGI declarations

The importer uses Python AST inspection, never import/evaluation. It recognizes one `import modal`, then one assignment to `modal.App()`, `modal.App(None)` or `modal.App("name")`, plus argument-free ASGI factory functions decorated with `@app.function(...)` and `@modal.asgi_app(...)`. The literal app name maps to `target.app`; one recognized function maps to `target.function`, while multiple functions require an explicit choice. The original Python file becomes `source.entrypoint`, and the profile is `asgi`. Imported Modal candidates require an explicit `target.mode: "existing"` or `"create"`, plus the exact workspace/environment; there is no silent existing-app default in this import route. Native manifest compatibility is unchanged.

The following literal source-owned settings are accepted without converting them into generic build/resources fields:

| Original Python construct | Accepted static subset |
| --- | --- |
| `modal.Image.debian_slim(...)` | No positional arguments; optional `python_version="major.minor"`. |
| Chained `.pip_install(...)` / `.apt_install(...)` | One or more literal package-name/specifier strings using the restricted package character grammar; no URLs, option strings, keywords or computed expressions. Packages are not installed during discovery. |
| Image assignment / `app.function(image=...)` | A unique previously declared image variable or an inline supported image expression. |
| `app.function(...)` | Optional `image`, `cpu`, `memory`, `timeout`, `min_containers`, `max_containers`, `scaledown_window`; numeric settings are literal nonboolean numbers from 0 to 1,048,576. Provider semantics remain in the original code, not a claim that all such values are live-valid. |
| `modal.asgi_app(...)` | No positional arguments; optional literal boolean `requires_proxy_auth`. Absence/false produces an exposure/authentication warning, not a fabricated authentication guarantee. |

Top-level docstrings are ignored. Function bodies are retained without execution or semantic analysis. Other top-level imports/effects, repeated/out-of-order imports, shadowed declarations, computed settings, unknown/duplicate decorators, function parameters/defaults/annotations and unsupported image/function options are rejected. Function bodies may still contain arbitrary application behavior: the source-trust review remains mandatory. Richer Modal packaging can use a reviewed native manifest instead of this bounded importer, subject to the same adapter contracts.

Static recognition does not prove function behavior, endpoint authentication, resource use or production readiness. No secret values are inferred from Python or moved into generic configuration. Source-owned package downloads, code execution and provider billing occur only in the later deliberately authorized deployment path.

### Daytona existing snapshot path

When a selected directory has no recognized candidate, Daytona can present an explicit snapshot-based choice. The user supplies the snapshot identifier and exact target/mode, name and source-trust answers. There is no universal Daytona YAML importer. An existing snapshot label is **not** asserted to be immutable: explicit remote inspection must resolve and bind the immutable snapshot/image receipt before an executable plan can be created. Project Dockerfile builds instead require an explicit new `snapshot_name`.

### Unsupported formats

Kubernetes/Helm, CI workflows, arbitrary application YAML/JSON and unknown files have no semantic importer. An explicit selection is reported as unsupported, not executed or renamed into an Onedeploy manifest. Ordinary unknown files may remain part of the selected source inventory without being recognized deployment inputs. Selecting a template is separate new-project work, never a substitute for the user's existing application.

## Typed resolution and provenance

Resolution accepts typed nonsecret answers and environment references only. It can select an inferred container profile differently, subject to adapter support, but cannot overwrite an explicit imported target/runtime declaration. Unsupported mappings remain blocking. Missing fields, conflicting choices and unsupported capabilities produce separate structured lists; the aggregate status prioritizes unsupported, then conflict, then missing input.

Each normalized leaf has provenance with `field`, source `path`, `location`, importer/version, source digest, mapping, `origin` (`explicit`, `inferred`, `user-resolved`) and ambiguity metadata. Locations are importer key/AST labels, not guaranteed source line/column coordinates. Some normalization metadata is labeled inferred. Answer provenance contains no raw value and uses an empty source path/digest; the full typed answers have a separate digest in the profile. Unresolved issues are reported separately, not hidden in a success flag.

Discovery binds the selected path, provider, candidates and filtered source inventory. Profiles include the original discovery, selected candidate, typed answers, answers digest, normalizer version and normalized manifest. Validation re-inventories source and replays the importer/resolver, not merely a caller-editable checksum. Changing input bytes, source inventory, provider or relevant answers invalidates the affected artifacts/plan. These hashes establish consistency, not external user authorization.

## Portable reuse is separate from discovery evidence

`export --profile` and `import --intent --path` carry a separate [non-executable portable intent](PORTABLE_INTENT.md), not a relocated local profile. Export strips original source identity, trust/approval, provider target IDs, storage IDs, snapshot/build names, readiness URLs and runtime literals. Import discovers the destination afresh and compares only the transported static subset; changed application bytes still need fresh code review and a newly bound/approved plan. Exact required fields, drift handling and deliberately omitted settings are documented in the intent contract.

## Bounds, exclusions and parser limits

| Boundary | Implemented limit |
| --- | --- |
| Individual parsed configuration | 1 MiB, UTF-8 regular file |
| Saved discovery/profile/answers artifact | 16 MiB, strict JSON, private POSIX permissions |
| Traversed entries | At most 10,000 directory entries, including visited excluded entries |
| Inventoried file bytes | At most 512 MiB total |
| Traversal depth / decoded structure depth | 24 |
| Candidate inputs / resulting candidates | 128 each |
| Decoded structure / Python AST nodes | 50,000 |
| Traversal time | Cooperative 15-second checkpoint limit per inventory pass, not a hard parser/OS I/O deadline |

Filesystem roots and the current user's home directory cannot be selected as project roots. Paths must stay within the selected scope; symlink paths, traversed symlinks and nonregular files are rejected. There is no ancestor-directory discovery. JSON duplicates/non-finite constants, YAML aliases/duplicate or non-string keys/custom unsafe tags and unsupported decoded objects fail. TOML is parsed as data. No YAML constructor, Python AST node, URL or shell-looking string becomes an instruction to execute.

Excluded names: `.git`, `.onedeploy`, `.venv`, `venv`, `__pycache__`, `.pytest_cache`, `node_modules`, names beginning `.env`, and names ending `.pem`, `.key`, `.p12`, `.pfx`. Passive discovery does not evaluate `.gitignore` or `.dockerignore` and is not a complete secret scanner. Its source-only binding is deliberately conservative: even a Git-ignored file that is not covered by the fixed exclusions can invalidate discovery. The later core upload inventory still applies Git ignore checks and its own sanitization/exclusions, so discovery inventory is not a claim that every inventoried file will upload. Keep unrelated/private material outside the selected scope. Unknown source content is hashed, not echoed as provenance, but it can still be part of a later reviewed upload under the core's separate bundle rules.

The traversal deadline is checked cooperatively; parsers are bounded by input size/depth/node checks, not run in a time-limited subprocess. These limits reduce accidental and malicious resource use but do not establish a hostile-input sandbox or a security certification. See [verification](VERIFICATION.md) for actual regression evidence.
