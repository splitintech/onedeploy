# Onedeploy

A local, project-agnostic deployment CLI and optional terminal UI for Railway, Daytona and Modal. The command interface is suitable for human developers and local coding agents. No LLM account or hosted Onedeploy service is required.

**Experimental:** offline contract tests are not live deployment qualification. Read the provider capability documentation before selecting a profile. Arbitrary applications are not guaranteed to run unchanged on every provider.

## Fork and install locally

Fork `splitintech/onedeploy` on GitHub, then clone your fork. The initial implementation lives on `feat/onedeploy-initial` until maintainers decide to merge it. Include that branch when creating your fork (do not copy only `main`); otherwise fetch the reviewed feature branch from the upstream repository before switching to it.

```sh
git clone https://github.com/YOUR_ACCOUNT/onedeploy.git
cd onedeploy
git switch feat/onedeploy-initial
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[tui]'
onedeploy --help
```

Use Python 3.11 or newer. The core is portable Python; filesystem protection and provider integrations must be qualified on the platform you use. Initial local verification is on Linux/Python 3.12. CI declares a Linux Python-version matrix; check the actual CI run before assuming other versions passed. Provider CLIs/SDKs are separate prerequisites and their authentication remains under your control. Onedeploy does not log you in or generate credentials automatically.

No package-registry release is implied. Do not assume an identically named package on a public registry is this repository; use a reviewed checkout or built wheel from this source.

## First plan

```sh
onedeploy init --template docker-http
```

Inspect the generated manifest and `.env.example`. Select explicit provider resource identifiers, replace placeholders and create a private `.env` outside the source upload context where practical. Never commit the populated file. Commit reviewed application source before planning; dirty inputs need an explicit opt-in and are hashed rather than silently ignored.

Create the file explicitly and restrict its permissions before validation (the example is placeholders only):

```sh
cp .env.example .env
chmod 600 .env
# Edit .env locally, replace manifest target placeholders, and review source.
# Ensure .env is ignored by Git before committing application source.
```

```sh
onedeploy config validate --manifest onedeploy.yaml --env-file .env --json
onedeploy plan --manifest onedeploy.yaml --env-file .env --out preview.json --json
```

The command above is a purely local, non-executable preview. To bind an executable plan, explicitly permit read-only provider inspection using existing authentication:

```sh
onedeploy plan --manifest onedeploy.yaml --env-file .env --inspect-remote --out plan.json --json
```

Planning is not deployment. Review target identity, source bundle, runtime variables, provider-specific prerequisites and operation list. A saved plan contains no runtime secret values. A plan ID is not proof of permission: agents must obtain authorization through their own trusted policy before passing an approval flag.

```sh
onedeploy deploy --plan plan.json --approve-plan PLAN_ID --env-file .env --json
```

This is a real mutating operation when authorized and configured, not a simulation. It may incur charges and transmit selected runtime variables to the chosen provider. Do not run it until you intend that exact deployment. A timeout may leave a running remote operation; inspect and reconcile rather than repeatedly executing deploy.

## Terminal interface

```sh
onedeploy tui
```

The TUI can load a provider-specific manifest or guide creation of a fresh generic project with provider/profile and exact target fields. Changing provider clears incompatible IDs and approval rather than reusing them. Prerequisite diagnosis, typed variable entry, saved plan review, logs and recovery use the same engine. Secret input is masked. Long operations run off the UI thread. Cancelling local progress does not guarantee that a provider rolled back or stopped charging; follow the resulting journal state.

## Agents and automation

Use `--json` and explicit paths. Parse the versioned result envelope and exit code instead of scraping terminal messages. stdout is machine output; stderr is for diagnostics. Never construct shell strings by interpolating user variables. Do not capture `.env` contents in agent conversations or logs.

```sh
onedeploy schema --json
onedeploy doctor --provider railway --json
onedeploy status --deployment DEPLOYMENT_ID --json
onedeploy resume --deployment DEPLOYMENT_ID --plan-out recovery-plan.json --json
```

To watch logs, the CLI can repeatedly request bounded snapshots:

```sh
onedeploy logs --deployment DEPLOYMENT_ID --follow --poll-interval 5 --json
# Optional bounded observation: add --max-polls 12.
```

Each changed snapshot is one versioned JSON envelope on stdout, so `--follow --json` uses newline-delimited JSON. Identical consecutive snapshots are suppressed, but changed snapshots may overlap and this is not a lossless incremental stream. Ctrl-C stops polling with a final cancellation envelope and exit code 8; it does not stop the deployment. Provider logs may contain private data and redaction is best-effort.

Recovery can return a separately approvable plan only when its journal proves no provider operation began. Otherwise it returns reconciliation evidence without retrying writes. `--plan-out` writes a file only when a real recovery plan is returned. Rollback requires compatible recorded releases and explicit compatibility assertions. Modal targets a known vN release; Railway targets a retained prior local deployment; Daytona additionally requires an explicit new replacement sandbox name. See the recovery guide before executing:

```sh
onedeploy rollback --deployment DEPLOYMENT_ID --to v3 --plan-out rollback-plan.json --json
```

For an uncertain outcome, `resume` may return an observation ID. Only after an operator independently resolves the outcome may a caller pair `--resolve-with-observation OBSERVATION_ID` with `--outcome operator-confirmed-completed` or `--outcome operator-confirmed-not-executed`. This records an operator assertion, not independently verified truth. Knowing the observation ID is not authorization; agents must preserve their own approval policy. The TUI exposes the same explicit assertion with a separate consent control; it does not infer or make the assertion on your behalf.

Recovery and rollback are not permission bypasses. They require known state and a reviewed plan. Unsupported operations fail explicitly rather than pretend to succeed. A built-in MCP server and cloud-hosted agent control plane are not prerequisites for using the CLI.

## Application responsibilities

Onedeploy does not automatically migrate your database, send emails/voicemails, seed production data, create OAuth applications, create provider credentials, change DNS, enable public application flags, or delete cloud resources. Applications must supply safe startup/readiness behavior. A health probe does not prove data integrity, authentication, model quality, or production suitability.

See [the plan](../Onedeploy.md), [security boundaries](../SECURITY.md), [contributing](../CONTRIBUTING.md), and the provider contract documentation.

## Reproducible development dependencies

`requirements-dev.lock` records the tested core/TUI/test dependency resolution. `requirements-all.lock` additionally records the optional Daytona/Modal operator SDKs. These are version-pinned constraints, not wheel-hash supply-chain attestations. Use the appropriate file with `pip install -c ...`; do not assume a Linux-tested transitive lock is qualified on every platform.

```sh
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m pytest -q
python -m build --no-isolation
```

Install the optional provider support deliberately, after reviewing its prerequisites:

```sh
python -m pip install -c requirements-all.lock -e '.[tui,daytona,modal]'
```

The Railway CLI is a separate official tool; the provider guide specifies the checked version. Onedeploy does not download or upgrade it automatically.

## Guided project creation

Use the TUI's new-project controls or the equivalent CLI. `init --provider` chooses a compatible default template; explicitly selecting an incompatible template is rejected. Target options contain nonsecret IDs only:

```sh
onedeploy init --provider daytona --directory ./new-model --account ORGANIZATION_ID --sandbox NEW_SANDBOX_NAME --snapshot-name NEW_SNAPSHOT_NAME
onedeploy init --provider railway --directory ./new-http --account ACCOUNT_ID --project PROJECT_ID --environment ENVIRONMENT_ID --service SERVICE_ID
onedeploy init --provider modal --directory ./new-asgi --account WORKSPACE_NAME --environment ENVIRONMENT_NAME --app NEW_APP_NAME --function web
```

Creation writes a fresh local project only, not cloud resources. Replace remaining placeholders and review all generated source before planning. Runtime credentials are never accepted as target flags.
