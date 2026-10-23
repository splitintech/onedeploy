# onedeploy
A CLI tool that automates your inference deployments

## Local developer tool

Onedeploy is a project-agnostic, plan-first CLI with an optional terminal UI for supported Railway, Daytona and Modal workflows. Local agents use the same JSON CLI; no LLM provider is required.

**Experimental / offline-tested.** This source is not a claim of live provider or production qualification. Supported profiles differ by provider; inspect the capability guide before deploying. No provider login, resource creation or package-registry release occurs just by installing the tool.

### Fork and run locally

From your reviewed fork/checkout (the initial work is on `feat/onedeploy-initial`):

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[tui]'
onedeploy --help
onedeploy tui
```

Python 3.11+ is required; initial local verification uses Linux/Python 3.12. Provider CLIs/SDKs and existing authentication are separate prerequisites. Use the repository installation above, not an unverified similarly named registry package.

- [Getting started and fork workflow](docs/GETTING_STARTED.md)
- [Provider contracts and capability limits](docs/provider-contracts.md)
- [Configuration and secret handling](docs/CONFIGURATION.md)
- [Agent CLI contract](docs/AGENT_CLI.md)
- [Recovery and rollback](docs/RECOVERY.md)
- [Implementation and qualification status](docs/IMPLEMENTATION_STATUS.md)
- [Original implementation plan](Onedeploy.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Generate and review a plan before an explicit deploy. Plan IDs bind intent, not proof of human authorization. Do not put credentials in arguments, source or plans, and do not automatically retry an uncertain write.

Additional references: [field reference](docs/CONFIGURATION.md), [compatibility worksheets](docs/COMPATIBILITY_WORKSHEETS.md), [troubleshooting](docs/TROUBLESHOOTING.md), [version and migration policy](docs/VERSIONING.md), and [dependency license review](docs/DEPENDENCY_LICENSES.md).
