# Changelog

## CLI-first provider revision (unreleased)

- Default Daytona to the installed official CLI 0.226.0 and its existing browser-authenticated user/configuration context, without requiring the optional Python SDK or a separate API key.
- Preserve explicit `target.transport: sdk` compatibility, with source-bound transport selection and no fallback. Old plans must be regenerated.
- Distinguish missing/incompatible tools from unchecked authentication; guided imports request explicit transport choice when sensitive runtime injection or authenticated probes require SDK.
- Reject CLI secrets by default. Allow only explicitly nonsecret runtime values through process-visible CLI arguments, with plan disclosure. CLI logs and authenticated probes remain unsupported.
- Bind provider organization, immutable snapshot, volume and sandbox identities before writes; project public fields only and preserve uncertainty on failed writes.

## 0.2.0a1 (unreleased, experimental opt-in)

- Add passive existing-project discovery, deterministic resolution and optional private saved profiles, without requiring handwritten Onedeploy YAML or overwriting original application files.
- Add non-executable portable intent `export`/`import`, with allowlisted configuration references, fresh destination discovery/trust/target binding and no carried source identity, runtime values or approval. Local profiles remain source-bound.
- Add JSON `discover`, `resolve`, `plan --profile` and versioned discovery/answers/resolution/profile schemas. Preserve the version-1 result envelope and manifest commands; add typed resolution errors with exit codes 11–13 and optional structured `error.details`.
- Add the opt-in `tui --wizard --path ...` provider → project → review workflow, with explicit code/target review, separate prerequisite/authentication states, deliberate plan-bound approval and Advanced recovery. Plain `tui` retains the manifest interface.
- Bind profile provenance and selected source evidence into plans and recheck at execution. Runtime secrets remain separate from discovery answers, profiles and approval identifiers.
- Publish importer field/limit documentation, profile/migration guidance, a JSON-only agent walkthrough and an honest U1–U5 acceptance matrix. Correct historical feature-branch installation guidance to `main`.

The final increment passed 506 offline tests, independent review and clean rebuilt-wheel relocation checks for all three providers plus nine schemas and `pip check`; see [verification](docs/VERIFICATION.md). The wizard is not promoted to the default; live provider, representative human usability, screen-reader and untested OS qualification remain separate gates. No source publication, registry release, login or real deployment is implied.

## 0.1.0 (unreleased)

Initial experimental implementation of the approved Onedeploy plan:

- Strict project-agnostic manifests and non-executing dotenv configuration.
- Plan-bound source/configuration, explicit approvals and provider identity checks.
- Railway, Daytona and Modal adapters with explicit capability differences.
- Private operation journals, receipt-aware recovery, retained-source Railway rollback, replacement-snapshot Daytona rollback and known-release Modal rollback.
- Project snapshot builds, explicit existing-volume attachments, dependency/TLS contracts and bounded read-only authentication checks.
- Machine-readable CLI, bounded log snapshot following and guided Textual provider/target setup with saved plans and recovery.
- Generic examples, package metadata, offline CI and regression tests.

This is a source-development version, not a registry publication or a live-provider certification. The verification report records exact checks and remaining operator qualification. No production resources, credentials, migrations or application sends were exercised while implementing this release.
