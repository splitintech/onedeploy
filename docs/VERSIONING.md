# Supported versions and migration policy

The current increment is experimental version 0.2.0a1; the initial merged baseline was 0.1.0. Manifest and machine-envelope contracts are version 1. Plans bind their engine/adapter/schema versions, exact source/configuration and remote preconditions; a saved plan is not a portable indefinite deployment permission.

- Provider compatibility is pinned and fail-closed: Railway CLI 5.71.0, Daytona CLI 0.226.0 (default), optional explicit Daytona SDK 0.226.0 and Modal CLI/SDK 1.6.2. Upgrade adapters and their official-interface contract tests together. Do not bypass a version mismatch to force deployment.
- Python 3.11–3.13 are the CI targets. Actual local evidence is recorded separately in VERIFICATION.md; a configured matrix is not a passing matrix. Other interpreter/OS combinations are unqualified until tested.
- Backward-compatible fields may be added within a schema version only if existing semantics remain unchanged. Breaking behavior requires a new schema/major contract and migration notes. Unknown fields must continue to fail rather than being ignored.
- No automatic state or manifest migration runs during installation. After an upgrade, retain private journals and source artifacts, inspect interrupted operations, regenerate plans under the new version and obtain fresh approval. Never edit hashes in old plans to make them execute.
- A rollback of application code is separate from downgrading Onedeploy. Preserve journals and do not interpret an older engine's inability to read new state as permission to retry a write.
- Before dependency upgrades, rerun unit/contract/TUI/package tests, regenerate license evidence, build in a fresh environment and record provider live qualification separately. Package-registry publishing remains a separate explicit release action.

## Experimental discovery/profile rollout

The three-step wizard is opt-in with `tui --wizard`; plain `tui`, `--manifest`, `config validate`, `inspect` and existing deployment/recovery commands remain available. No default-switch or removal date is committed in this source increment. Promotion requires the acceptance gates in [UX_ACCEPTANCE.md](UX_ACCEPTANCE.md), including representative human and JSON-only agent tasks, not just an implementation merge.

Discovery, answers, resolution and saved-profile schemas are retrieved with `schema --kind discovery|answers|resolution|profile|intent --json`. They are distinct from the version-1 manifest and generic result envelope. A profile is a versioned local resolution artifact, not an arbitrary provider file or an indefinite deployment permission. Do not edit hashes or convert a version number by hand. Re-discover and resolve changed inputs, then generate and approve a new plan.

Original project files are preserved. There is no automatic in-place conversion of Compose, Railway configuration or Modal Python to a new application. Existing manifests need not be renamed or migrated to use the fallback. Saved profiles contain local paths and target identifiers: copy/review them as operational metadata, not as portable credentials or pre-approved deployments. Consult [configuration](CONFIGURATION.md#discovery-and-optional-profiles) before moving a profile to another checkout or machine.

`export --profile` produces a separate version-1 non-executable portable intent. `import --intent --path` performs fresh discovery and requires new source trust and destination bindings before producing a new local profile. No old source hash, provider target ID, credential, runtime value or plan approval is migrated. This is allowlisted partial configuration portability, not automatic profile relocation; see [the exact intent contract](PORTABLE_INTENT.md).

## Daytona transport migration

Daytona adapter-version `2-*` plans bind the chosen `target.transport` (`cli` by default, `sdk` only when explicitly selected). Regenerate a source-bound plan and obtain fresh review after changing transport or moving from the SDK-era adapter; do not replay or edit old plans. CLI uses the existing browser session in the invoking user's CLI configuration context. SDK remains separately authenticated with `DAYTONA_API_KEY`; missing CLI authentication or unsupported CLI features never trigger SDK fallback.

The [0.226.0 official release](https://github.com/daytona/clients/releases/tag/v0.226.0) includes CLI binaries. Its [comparison with 0.225.0](https://github.com/daytona/clients/compare/v0.225.0...v0.226.0) changes the CLI toolchain/dependency pin, not the reviewed commands. Existing SDK verification records are historical and do not qualify the new CLI transport. See [capability limits](provider-contracts.md#daytona) before migrating services with runtime variables or authenticated checks.
