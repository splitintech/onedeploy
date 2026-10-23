# Contributing

Fork the repository and create a feature branch. Use Python 3.11 or later in an isolated virtual environment. Install the tested build/test environment with `python -m pip install -r requirements-dev.lock` and `python -m pip install --no-deps -e .`, then run `python -m pytest -q` and `python -m build --no-isolation`.

Provider changes need official interface references, capability documentation, mocked transport contract tests, failure and uncertainty tests, and secret-redaction tests. Do not add live provider calls to default tests or CI. Use synthetic IDs and `.env.example` placeholders only. Never copy private application source into examples.

Do not silently broaden capabilities or translate an uncertain write into a retry. Schema/JSON-output changes need versioning and compatibility notes. TUI changes must use the shared engine and test cancellation and repeated interactions.

Submit a focused pull request. Include the commands run, their actual results, and any checks not run. Source publication is not authorization to deploy, change accounts, run migrations, or publish a package.
