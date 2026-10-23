# Onedeploy

A local, project-agnostic deployment CLI and optional terminal UI for Railway, Daytona and Modal. The command interface is suitable for human developers and local coding agents. No LLM account or hosted Onedeploy service is required.

**Experimental:** offline contract tests are not live deployment qualification. Read the provider capability documentation before selecting a profile. Arbitrary applications are not guaranteed to run unchanged on every provider.

## Fork and install locally

Fork `splitintech/onedeploy` on GitHub, then clone your fork. The initial implementation is merged into `main`. Use a reviewed checkout containing the experimental UX increment; source publication and package publication are separate. The historical `feat/onedeploy-initial` branch is not required.

```sh
git clone https://github.com/YOUR_ACCOUNT/onedeploy.git
cd onedeploy
git switch main
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[tui]'
onedeploy --help
```

Use Python 3.11 or newer. The core is portable Python; filesystem protection and provider integrations must be qualified on the platform you use. Initial local verification is on Linux/Python 3.12. CI declares a Linux Python-version matrix; check the actual CI run before assuming other versions passed. Provider CLIs/SDKs are separate prerequisites and their authentication remains under your control. Onedeploy does not log you in or generate credentials automatically.

No package-registry release is implied. Do not assume an identically named package on a public registry is this repository; use a reviewed checkout or built wheel from this source.

## Existing project: experimental three-step wizard

```sh
onedeploy tui --wizard --path /absolute/path/to/your/project
```

`--wizard` is the opt-in. Plain `onedeploy tui` retains the manifest interface during this experimental release. Discovery does not require a provider login, network access or a handwritten Onedeploy YAML file. A recognized file may still contain unsupported behavior; check [the importer matrix](IMPORTERS.md) before expecting deployment support.

1. **Choose provider.** Select Railway, Daytona or Modal. Check installed tools separately from authentication. Official setup guidance is available, but the wizard does not install tools, log in or create/store provider credentials. Existing Railway project/environment/service IDs, Daytona organization/sandbox settings and Modal workspace/environment/app settings are not interchangeable.
2. **Choose project.** Enter a project directory or supported file path. Review discovered candidates and choose the intended input/service. Choose the supported runtime profile explicitly when it is inferred, supply missing nonsecret answers, review code trust and select a private env file or use masked runtime input. **Add runtime variable references** lets you declare names, environment sources, types and requirements without putting values in the profile. Compose dependency/storage requirements are expanded into labeled fields, not a request to handwrite YAML. Incomplete or unsupported mappings stop before planning; switching providers requires new target choices. Discovery and resolution preserve original application files.
3. **Review and deploy.** Make a local preview first or explicitly enable read-only remote inspection using your existing authentication. Review source, exact target, changes, variable recipients, storage/ingress/lifecycle implications, cost uncertainty and verification limits. A local preview cannot execute. The deliberate **Deploy this plan** action binds the reviewed immutable plan without asking you to copy a plan ID. Changes require a new review.

Status and bounded log snapshots are available after execution. Advanced/recovery controls retain access to interrupted or uncertain operations. Cancelling local work does not promise remote rollback, deletion or a spending cap. Do not retry a deployment until its outcome is established.

Optional profiles can save a resolution for reuse; they are not mandatory application files and are not credentials. In Step 2, enter a saved profile JSON path and use **Load saved profile from this path** or **Discover project**. The current provider and source binding must match; source trust resets and you resolve again before reviewing a fresh plan. Keep profiles private, outside the source bundle or in excluded `.onedeploy`. These local profiles bind absolute paths. For another checkout/machine, [export a separate non-executable intent](PORTABLE_INTENT.md) and resolve it against freshly discovered source and newly confirmed destinations, rather than editing hashes. See [profile storage, portability and invalidation](CONFIGURATION.md#discovery-and-optional-profiles).

No importer converts an arbitrary application into every provider's packaging. For example, a Dockerfile does not become a Modal ASGI app automatically. No hidden database provisioning, migrations, application sends, public exposure, login or billing commitments are added to the three steps.

See [acceptance evidence](UX_ACCEPTANCE.md) for automated versus unrun human/platform checks. The wizard remains experimental until its promotion gates are met.

## Manifest fallback and optional new-project templates

Use this route for an existing Onedeploy manifest or when deliberately creating a new sample. `init` creates new local files; it is not an import of your application.


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

## Legacy terminal interface

```sh
onedeploy tui --manifest path/to/onedeploy.yaml
```

The fallback remains available, including guided creation of a fresh generic project, typed masked runtime input, prerequisite diagnosis, saved plans, logs and recovery. This legacy review screen uses its existing plan-ID confirmation; the opt-in wizard uses **Deploy this plan** instead. Both call the same planning/execution engine. Do not confuse template creation with importing an existing application.

## Agents and automation

Follow the [JSON-only discovery-to-execution journey](AGENT_CLI.md) for an existing project without authoring a manifest. Use `--json` and explicit paths. Parse the versioned result envelope and exit code instead of scraping terminal messages. stdout is machine output; stderr is for diagnostics. Never construct shell strings by interpolating user variables. Do not capture `.env` contents in agent conversations or logs.

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
