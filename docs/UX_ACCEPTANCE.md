# Experimental UX and Agent CLI acceptance matrix

This matrix traces the U1–U5 gates in [the design plan](../Onedeploy.md) to the current increment. **Implementation, an automated test, a passing aggregate run, human usability and live qualification are different claims.** Historical baseline tests do not prove new code. The final increment evidence below closes the recorded implementation/offline checks, not the separate external qualification and rollout gates.

The release posture is **experimental opt-in**: `onedeploy tui --wizard --path ...`. The manifest CLI and plain `tui` remain available. No package publication, merge, live deployment or promotion to default is implied by these documents.

[Verification](VERIFICATION.md) records **506 passing tests in 233.98 seconds**, final independent review with no remaining concrete blockers, and rebuilt installed-wheel relocation checks for all three providers plus all nine schemas and clean `pip check`. Environment: Linux/Python 3.12.14/Textual 8.2.8. Human usability, remote CI, other platforms and live/application qualification remain separate unrun gates.

## Requirement traceability

| Requirement | Implementation / contract to inspect | Acceptance evidence | Status / remaining gate |
| --- | --- | --- | --- |
| U1: versioned discovery, answers, resolution and profile contracts | `onedeploy/discovery.py`; `schema --kind ...`; [agent contract](AGENT_CLI.md) | Schema fixtures, valid/invalid envelope and artifact tests | Offline regression coverage passed in the final 506-test suite |
| U1: passive local-only discovery | [importer contract](IMPORTERS.md); discovery reader/parser | Guarded tests proving no provider/network/process/import calls and no source writes | Offline passive/no-write guards passed; no live calls needed |
| U1: explicit support matrix and fail-closed mapping | [per-format fields and rejections](IMPORTERS.md) | Independent synthetic accepted/rejected fixtures for every advertised subset | Offline regression coverage passed in the final 506-test suite |
| U1: secrets, malicious inputs and bounds | Safe YAML/JSON/TOML/AST parsing; inventory/path restrictions | Secret sentinel, unsafe tag/duplicate, symlink/escape, depth/size/count and unsupported input tests | Offline bounds/rejection tests passed. Cooperative traversal limit, not a hard parser sandbox or security certification |
| U2: deterministic resolution with typed questions | Discovery status, `resolve`, answer schema and provenance | Missing-target, conflicting-candidate/service and unsupported-mapping cases | Offline regression coverage passed in the final 506-test suite |
| U2: original source preservation and change invalidation | Discovery/profile bindings and core plan path | Original hashes unchanged, profile round trip, changed inputs/answers/source rejected | Offline regression coverage passed in the final 506-test suite |
| U2: optional profile and reference-only secret storage | [profile contract](CONFIGURATION.md#discovery-and-optional-profiles) | Private output-path/permissions, redaction, no runtime secret persistence | Local profile and portable-intent tests passed, including edited-intent binding guards. Fresh source/trust/target binding is mandatory; export is deliberately partial |
| U2: complete noninteractive agent journey | [JSON-only walkthrough](AGENT_CLI.md) | `discover → resolve → plan`, trusted-policy approval, simulated deployment/status/recovery | JSON-only fixture journeys and installed-package checks passed; actual cloud execution unrun |
| U3: existing-project three-step onboarding | `onedeploy/wizard.py`; [quickstart](GETTING_STARTED.md) | Provider → project/config → review; no handwritten mandatory manifest; legacy fallback | Offline regression coverage passed in the final 506-test suite |
| U3: prerequisites separate from authentication | Doctor/setup guidance; explicit read-only remote inspection | Tool-missing, version mismatch and offline-preview paths | Offline prerequisite/preview paths passed; no automatic login/credential persistence; live authentication unqualified |
| U3: approval bound to reviewed plan, no human hash entry | **Deploy this plan** and shared core | Local preview cannot execute; edits/back/provider changes clear review; source/precondition changes fail closed | Offline regression coverage passed in the final 506-test suite |
| U3: interrupted/repeated UI flows | Wizard workers, controls and Advanced recovery | Cancellation, repeat-click, pending-operation and recovery visibility tests | Offline regression coverage passed in the final 506-test suite |
| U3: keyboard, resize and accessibility | Textual wizard | Headless keyboard/narrow-terminal/resize coverage plus manual terminal and screen-reader checks | Headless keyboard/narrow/resize tests passed; manual screen-reader/terminal qualification unrun |
| U4: importer-to-provider integration | Existing adapters via normalized configuration | Each advertised importer/provider path; UI/CLI equivalent profile and plan evidence | Offline importer/provider paths, real API/CLI/wizard plan parity and three-provider installed-wheel relocation passed; provider live behavior unrun |
| U4: baseline regressions and independent review | Core/adapter/CLI/TUI tests; [verification](VERIFICATION.md) | Final centralized suite, schema applicability and security-focused independent review | Final 506-test suite and independent review passed; no remaining concrete blocker in reviewed scope, not a security certification |
| U4: honest release/readiness/recovery layers | Existing core, receipts and journals | Changed identity/secret/source/preconditions, failed builds/probes, uncertain writes, rollback tests | Final regressions passed, including real profile-origin recovery journals and source/resource-binding fixes; no live outcome claimed |
| U4: OS/provider/application qualification | [provider contracts](provider-contracts.md), [worksheets](COMPATIBILITY_WORKSHEETS.md) | Record exact OS/interpreter/provider versions and independently authorized live runs | Only recorded platforms qualify; live, macOS/Windows and private applications unrun |
| U5: docs, migration and fallback | README, quickstart, importers, agent CLI, configuration/version policy | Local link check; examples checked against actual schema/CLI; stale branch guidance removed | Final docs/API review, 13-call local and 15-call relocation example smokes passed; 16-file local link check passed |
| U5: independent newcomer and agent acceptance | New-user task and JSON-only agent task | Provider choice, existing-project discovery, conflict resolution, review, simulated approved execution without handwritten YAML | Representative independent-user usability unrun; automated evidence must not replace it |
| U5: staged release/promotion | `--wizard` opt-in; retained manifest route | U1–U4 evidence plus representative usability sign-off | Default promotion intentionally not performed |

## Test source index

These are inspectable test definitions, not proof that an unrecorded final run passed:

- [Discovery/importers](../tests/test_discovery.py): passive execution guards, strict schemas, each supported format, unsafe/unsupported semantics, ambiguity, provenance/source invalidation, private saves and secret-shaped input handling.
- [Portable intent](../tests/test_discovery_intent.py): allowlisted reference-only export, fresh-source matching, missing trust/target/storage/snapshot bindings, drift and private files.
- [Agent CLI](../tests/test_agent_discovery_cli.py): JSON schemas/envelopes, typed exit codes, no-prompt round trips, command compatibility, source preservation, profile routes and exclusive output behavior.
- [Core profile bridge](../tests/test_profile_core.py) and [recovery-chain regression](../tests/test_profile_recovery_chain.py): shared planner, no generated manifest, approval boundaries, stale/tampered evidence, secret separation and profile-origin recovery through actual journals.
- [Wizard unit flows](../tests/test_wizard.py): deliberate click approval, provider/back/edit invalidation, cancellation/repeated submission, recovery consent, keyboard/narrow-terminal/resize paths and error focus.
- [Wizard integration](../tests/test_wizard_integration.py): real Compose dependency/storage resolution through the shared planner, same-profile plan parity, saved-profile trust/source checks and provider mismatch rejection.
- Existing [core](../tests/test_core.py), [providers](../tests/test_providers.py), [configuration](../tests/test_config.py), [verification layers](../tests/test_verification.py), [legacy TUI](../tests/test_tui.py), [CLI](../tests/test_cli.py) and [templates](../tests/test_templates.py) preserve the baseline contracts.

## What counts as evidence

- Record exact test commands, totals, platform and interpreter in [VERIFICATION.md](VERIFICATION.md). Name a focused subset as a subset, not the full suite.
- Synthetic/provider-transport fixtures may establish offline contract behavior, including simulated approved execution. They do not establish live authentication, provider acceptance, billed cost, ingress, data durability, model quality or application suitability.
- A headless keyboard test does not establish real screen-reader compatibility, terminal rendering on all platforms or success for a representative new user.
- Plan/profile hashes bind inputs and intent. They do not certify trusted code or prove that a human authorized an external agent to deploy.
- Unknown outcomes remain unknown. Safe reconciliation is a result; automatic replay of an uncertain write is not acceptable evidence of completion.

## Deferred or excluded, not silently implemented

No universal Kubernetes/Helm/CI importer, multi-service Compose orchestrator, arbitrary Python evaluator, automatic Docker-to-Modal conversion, generated credential/login workflow, remote privileged agent server or provider-account/database/DNS provisioning is promised. Profiles remain absolute/local-source-bound. Separate portable intent export/import carries only reusable allowlisted settings; it does not carry executable authority, target IDs or old source identity. Another checkout/machine requires fresh discovery/resolution, trust and destination bindings rather than hash editing. Passive discovery has a cooperative traversal deadline, not a parser subprocess sandbox; it does not evaluate Git ignore rules, while the upload core still does. Optional keychain/MCP integrations remain future work. Real OSM, CPU Voice and Slybroadcast integration/qualification is not replaced by sanitized public worksheets.
