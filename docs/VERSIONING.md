# Supported versions and migration policy

The initial package is experimental version 0.1.0. Manifest and machine-envelope contracts are version 1. Plans bind their engine/adapter/schema versions, exact source/configuration and remote preconditions; a saved plan is not a portable indefinite deployment permission.

- Provider compatibility is pinned and fail-closed: Railway CLI 5.71.0, Daytona SDK 0.226.0 and Modal CLI/SDK 1.6.2. Upgrade adapters and their official-interface contract tests together. Do not bypass a version mismatch to force deployment.
- Python 3.11–3.13 are the CI targets. Actual local evidence is recorded separately in VERIFICATION.md; a configured matrix is not a passing matrix. Other interpreter/OS combinations are unqualified until tested.
- Backward-compatible fields may be added within a schema version only if existing semantics remain unchanged. Breaking behavior requires a new schema/major contract and migration notes. Unknown fields must continue to fail rather than being ignored.
- No automatic state or manifest migration runs during installation. After an upgrade, retain private journals and source artifacts, inspect interrupted operations, regenerate plans under the new version and obtain fresh approval. Never edit hashes in old plans to make them execute.
- A rollback of application code is separate from downgrading Onedeploy. Preserve journals and do not interpret an older engine's inability to read new state as permission to retry a write.
- Before dependency upgrades, rerun unit/contract/TUI/package tests, regenerate license evidence, build in a fresh environment and record provider live qualification separately. Package-registry publishing remains a separate explicit release action.
