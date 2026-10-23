# Changelog

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
