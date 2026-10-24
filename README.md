# onedeploy

A local, plan-first deployment tool for humans and agents using supported Railway, Daytona and Modal workflows. No LLM account or hosted Onedeploy service is required.

**Experimental and opt-in.** The new three-step wizard and project-discovery CLI are an experimental increment, not a universal importer or live-provider certification. The manifest workflow remains available. See [implementation status](docs/IMPLEMENTATION_STATUS.md) and [acceptance evidence](docs/UX_ACCEPTANCE.md) for implemented, tested and pending work.

## Install from a reviewed checkout

The initial engine was merged into `main`; the historical `feat/onedeploy-initial` branch is not the installation path. Use a reviewed checkout containing this increment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[tui]'
onedeploy --help
onedeploy tui --wizard --path /absolute/path/to/your/project
```

Python 3.11+ is required. Provider tools and existing authentication are separate prerequisites. Installing Onedeploy does not log in, create credentials or cloud resources, or publish a package. Do not assume a similarly named registry package is this repository.

## Daytona uses your existing CLI session

Daytona defaults to official CLI **0.226.0** and an existing browser login in the same local user/configuration context. No Python Daytona SDK or separate API key is needed for this path. Build/create, start, inspection, status and unauthenticated readiness checks are supported. Only explicitly nonsecret runtime variables can use process-visible CLI arguments; sensitive/default-secret injection, CLI logs and authenticated checks are unsupported. Services needing those features must explicitly select `target.transport: sdk`, install SDK **0.226.0**, and configure its separate API key. There is no automatic SDK fallback. New source-bound plans are required after changing transport; old plans cannot be replayed. See [setup and migration](docs/GETTING_STARTED.md#daytona-cli-first-and-explicit-sdk-migration) and the [provider contract](docs/provider-contracts.md#daytona).

## Existing project, three steps

1. **Choose provider.** Review supported profiles and check installed tools. Authentication is not assumed from tool availability; provider inspection is a separate explicit action.
2. **Choose project.** Select a folder or supported configuration file, review passive discovery and resolve required choices. The tool does not demand a handwritten Onedeploy YAML file or overwrite your application.
3. **Review and deploy.** Review source, destination, operations and risks. A local preview cannot deploy. Explicit read-only provider inspection creates an executable plan; **Deploy this plan** approves the exact reviewed plan without typing a hash.

Recognition is not deployment support. Read the [exact importer mappings and limits](docs/IMPORTERS.md). Provider capabilities, code trust, runtime secrets and application qualification remain explicit.

Agents use `discover → resolve → plan → deploy` with `--json` through the same engine. Reading a plan ID is not authorization to execute it. See the [complete JSON-only journey](docs/AGENT_CLI.md).

The legacy route remains `onedeploy tui --manifest path/to/manifest.yaml` or `onedeploy plan --manifest ...`. New-project templates are optional and separate from importing an existing project.

## Guides

- [Getting started and fork workflow](docs/GETTING_STARTED.md)
- [Importer mapping, bounds and preservation](docs/IMPORTERS.md)
- [Profiles, configuration and secrets](docs/CONFIGURATION.md)
- [Agent CLI and typed outcomes](docs/AGENT_CLI.md)
- [Portable non-executable intent](docs/PORTABLE_INTENT.md)
- [Provider contracts and capability limits](docs/provider-contracts.md)
- [Recovery and rollback](docs/RECOVERY.md)
- [Implementation status](docs/IMPLEMENTATION_STATUS.md) · [U1–U5 acceptance matrix](docs/UX_ACCEPTANCE.md) · [Verification](docs/VERIFICATION.md)
- [Compatibility worksheets](docs/COMPATIBILITY_WORKSHEETS.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Version policy](docs/VERSIONING.md)
- [Design and historical plan](Onedeploy.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Dependency licenses](docs/DEPENDENCY_LICENSES.md)

Never put credentials in flags, source, discovery answers or plans. Review executable build/application code before use. A timeout may hide an accepted cloud operation: inspect and reconcile, never blindly retry a write.
