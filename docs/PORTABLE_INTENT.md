# Portable configuration intent

A saved **profile** is bound to absolute local paths, exact inventoried source and a deterministic resolution. Do not move it to another checkout/machine and edit roots or hashes. Use the separate **portable intent** export/import route to carry reusable configuration choices without carrying source identity, credentials, provider bindings or approval.

An intent is **non-executable**. It is not a saved plan or local profile, cannot be passed directly to `plan --profile` or `deploy`, and does not copy application files. Import performs fresh passive discovery of an explicitly selected local project, requests new source trust and destination bindings, and produces a new local profile. That profile still needs a new plan, explicit remote inspection for execution and fresh trusted authorization.

## CLI migration

Starting with a valid, still-current local profile:

```sh
onedeploy schema --kind intent --json
onedeploy export --profile /private/old-profile.json \
  --out /private/portable-intent.json --json
```

Copy/review the application source separately using your normal workflow. At the new checkout, ask for fresh typed questions first:

```sh
onedeploy import --intent /private/portable-intent.json \
  --path /absolute/path/to/new-project --json
```

The initial import normally exits with `input_required` (11). Review its questions, inspect the new source, and prepare a fresh answers file conforming to `schema --kind answers`. It must explicitly provide `trust_code: true` only after code review and every required destination field, including fields present in native source. Imported Daytona and Modal require an explicit `target.mode`. Choose fresh storage resource IDs, snapshot/build names and readiness origin when requested. The old intent supplies none of those identifiers.

```sh
chmod 600 /private/fresh-answers.json
onedeploy import --intent /private/portable-intent.json \
  --path /absolute/path/to/new-project \
  --answers /private/fresh-answers.json \
  --out /private/new-local-profile.json --json
onedeploy plan --profile /private/new-local-profile.json \
  --out /private/new-preview.json --json
```

Supply the newly selected private runtime env file to validation/planning when needed. The commands above create a non-executable local preview, not a deployment. Neither `export` nor `import` calls providers, executes source, logs in or transfers runtime credentials. Source trust and a plan ID are not substitutes for the embedding agent/system's authorization policy.

`--out` is optional for both export and import; without it the command returns a JSON result and writes no intent/profile file. Saves are private and exclusive: existing files/symlinks are not overwritten. Keep output outside application source or inside excluded `.onedeploy` to avoid invalidating discovery. CLI and wizard exports enforce this source-scope restriction using the validated local profile; imported local-profile saves enforce it against the fresh destination. The low-level inert intent writer can also be used without a source root, which does not authorize modifying project files. A saved intent is limited to 1 MiB; private JSON artifact reads use the shared bounded reader. Even without target IDs, variable names and relative structure can be operationally sensitive, so review before sharing.

## Wizard migration

In Step 2, **Optional portable intent (not an executable plan)** exposes:

- **Export portable intent**, after resolving the current local profile. The default file is `.onedeploy/intent.json` under the selected project; choose a fresh filename if it already exists.
- **Import intent into this discovered project**, after choosing the destination project and exact candidate. The wizard refreshes passive discovery and clears selected env-file/runtime values, source trust and remote-inspection consent before collecting fresh bindings.

Every subsequent Apply resolves through the intent-aware validator. Reusing a saved profile or imported intent never loads plan approval. A changed/ambiguous relative source selection cannot be forced through an answer override; correct the destination selection or review a fresh ordinary discovery/resolution.

## Version-1 data contract

Retrieve the strict schema with `schema --kind intent --json`. Top-level fields are `version: 1`, `kind: "intent"`, `provider`, `selection`, `expected`, `settings`, `requirements`, `warnings`.

| Field | Carried data / boundary |
| --- | --- |
| `provider` | The selected provider, not account authentication. Provider changes need a new ordinary resolution. |
| `selection` | Importer format, relative input path, relative source-root path and optional Compose service. The new project must contain exactly one matching candidate. Paths use the schema's restricted safe-relative subset, not absolute roots or `..`. Unsupported filenames fail rather than being rewritten. |
| `expected` | An allowlisted static candidate-manifest projection plus its questions. Import compares the freshly discovered projection, not arbitrary application behavior or all source bytes. |
| `settings` | Reusable non-target answers such as name/profile, supported resource/ingress/check/dependency declarations and runtime variable references. No runtime values, old candidate ID or source trust. |
| `requirements.target_fields` | Names of fields requiring fresh destination confirmation, not their previous values. There are no retained target hints. |
| `requirements.storage` | Mount names/paths/access/kind/writer/attachment shape, but no provider resource IDs. Fresh mounts must preserve the reviewed shape. |
| `requirements.snapshot` | `none`, `existing` or `build`, never an old snapshot identifier/build name. |
| `requirements.ready_url` | Whether a fresh credential-free readiness origin is required, not the previous URL. |
| `warnings` | Fixed reminders that intent is non-executable and new source/destination review is required. |

Allowlisted exports omit absolute source roots, source revision/inventory/digests, provider target identifiers, storage IDs, snapshot/build names, readiness URLs, free-form runtime commands, variable defaults and conditional `required_if` literals, credentials/runtime values, source trust, provider inspection receipts and plan/approval IDs. Variable declarations retain only name, type, secret/required flags and environment reference where present.

This is deliberately **partial configuration portability**, not an exact copy of every runtime decision. Answer-defined conditional `variables.NAME.required_if` rules and `runtime.command` overrides block export with `unsupported_capability`; declare that behavior in reviewed source and discover again rather than silently losing it. Source-owned defaults/conditions are not transported or compared, so the freshly reviewed destination source remains authoritative for them. Other deliberately omitted source/runtime behavior must also be reviewed again. Fresh answers override reusable settings before the ordinary resolver runs, but cannot silently replace a conflicting explicit source declaration. Required target/storage/readiness/snapshot confirmations are also derived independently from freshly parsed source; editing an intent’s requirement flags cannot suppress them. Never remove a requirement merely to make import pass.

## Drift, errors and security limits

- A missing/nonunique relative selection or changed transported static configuration returns `conflict` (12). Review a fresh normal discovery/resolution rather than editing expected evidence to force a match.
- An unsupported destination-source mapping or an unexportable answer-defined conditional variable/startup-command rule returns `unsupported_capability` (13). Unsupported behavior cannot be waived through answers.
- Missing fresh trust/target/storage/snapshot/readiness input returns `input_required` (11). The structured resolution fields remain under `error.details`, with `result: null`.
- Malformed intent/answers return `invalid_config` (2). Exporting a stale local profile returns `stale_plan` (9).
- No source hashes are transported. Application code may have changed even when its static projected configuration matches. Import therefore requires fresh review, creates fresh local source bindings and never preserves old approval.

The [passive parser/traversal limits](IMPORTERS.md#bounds-exclusions-and-parser-limits), provider capability restrictions, runtime-secret handling and [recovery boundaries](RECOVERY.md) remain unchanged. This feature does not establish live-provider, production, cross-platform or representative-user qualification; see [verification](VERIFICATION.md).
