# AutoDeploy engineering preview

**Implemented:** offline source-bound plans, Python rollout engine, durable single-host state, explicit Daytona SDK boundary, and a reusable image-and-plan GitHub workflow. **Not enabled:** unattended paid GPU rollout, application routing, a background autoscaler, or production migration. No cloud account or registry publication happens when you install/import the library or run the default tests.

## Install from this reviewed branch

```sh
git clone --branch autodeploy https://github.com/splitintech/onedeploy.git
cd onedeploy
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
onedeploy autodeploy --help
python -m pytest -q
```

Use Python 3.11–3.13, as for existing OneDeploy. This is a source checkout, not a claim that an AutoDeploy package has been published to PyPI. Existing `onedeploy plan/deploy` and CLI-first Daytona behavior are unchanged.

## First offline plan

```sh
onedeploy autodeploy init --output /tmp/autodeploy.json --json
```

Edit the JSON with the real `deployment_id`, GitHub `repository`, deployment `branch`, provider account identifier in `target`, explicit CPU/RAM/disk/GPU settings, total worker limit and reviewed budget. Template values are placeholders, not verified provider resources. A limit of one worker cannot perform overlapping replacement of an already-running worker; use a reviewed limit permitting the candidate plus the old worker if zero downtime is required.

```sh
onedeploy autodeploy validate --config /tmp/autodeploy.json --json
onedeploy autodeploy plan \
  --config /tmp/autodeploy.json --source /path/to/application \
  --commit FULL_40_CHARACTER_COMMIT_SHA \
  --image ghcr.io/owner/application@sha256:FULL_64_CHARACTER_DIGEST \
  --model-digest MODEL_MANIFEST_SHA256 \
  --evidence-digest TEST_EVIDENCE_SHA256 \
  --out /tmp/rollout-plan.json --json
```

Replace the uppercase markers; these are explanatory examples, not executable identifiers. The source must be clean, including untracked files, and match the configured origin/branch and exact commit. Keep output outside the checkout or under its ignored `.onedeploy/` directory. JSON is strict and bounded; duplicate fields, unknown configuration fields and mutable image references fail. Existing output files are never overwritten.

The plan binds commit/tree, configuration, model/evidence digests and image digest. A digest identifies evidence; it does not prove test success or user authorization. The plan's `id` is an integrity identifier, not a credential or permission grant.

## CLI operations and status

| Operation | Current behavior |
| --- | --- |
| `init`, `validate`, `plan` | Implemented offline; no provider calls |
| `status --deployment ID --state /durable/controller.sqlite` | Read-only view of existing controller state |
| `apply --plan FILE --source PATH --approve-plan ID --state FILE` | Validates source/approval, then fails closed with `unsupported_capability` until a built-in qualified live controller is enabled |
| `reconcile`, `rollback`, `drain` | Reserved CLI entrypoints, intentionally capability-gated; not advertised as live operations |

The Python library below supplies lifecycle operations for a trusted application integration. CLI gates must not be removed merely to make a demo return success.

## Python integration

```python
from onedeploy.autodeploy.models import DeploymentConfig, ReleaseSpec
from onedeploy.autodeploy.state import StateStore
from onedeploy.autodeploy.reconciler import Reconciler

config = DeploymentConfig.from_dict(reviewed_config)
spec = ReleaseSpec.from_dict(reviewed_release)
store = StateStore('/durable/controller.sqlite')
engine = Reconciler(store, qualified_adapter)
release = engine.plan(config, spec)
# Authorization is supplied by the host application's trusted policy, never an LLM plan ID.
result = engine.apply(release['release_id'], approved=True)
```

`qualified_adapter` is an explicit trusted application integration, not an arbitrary module named by configuration. `sdk_adapter` in `onedeploy.autodeploy.daytona` provides concrete Daytona operations using an **isolated `daytona==0.231.0` environment** and verified account identity. Read its signature and [worker protocol](../examples/daytona-gpu-autodeploy/README.md): callers must supply authenticated application qualification/drain callbacks and a live policy enforcing budget, worker count and lifecycle bounds. No implicit SDK upgrade or fallback from existing 0.226.0 occurs.

The engine performs snapshot → candidate → qualification → desired-release CAS → old-worker drain/delete. The durable `desired_release` is the routing contract: the integrating stable API must route new jobs from this pointer, lease in-flight jobs, and honor drain admission rules. Updating this record does **not** itself reconfigure Railway, a load balancer or a client URL.

## Reusable GitHub workflow

`.github/workflows/autodeploy.yml` is opt-in `workflow_call` only. It accepts a trusted deployment branch, full reviewed tooling SHA, tracked application config/Dockerfile/test script, immutable model digest and approved GHCR image destination. It runs only for a push on that exact branch. Fork pull requests cannot deploy through this path.

It tests tooling/application, builds an image from the committed Git archive, pushes a commit-tagged image, resolves its digest, produces a source-bound rollout plan and uploads evidence. It has **no Daytona secret**, does not provision a candidate or promote traffic. Calling it will publish a container to the configured registry and may incur registry/Actions costs, so review before enabling.

Example caller, to be placed in the application repository after review:

```yaml
name: Prepare GPU release
on:
  push:
    branches: [deployment]
permissions:
  contents: read
  packages: write
jobs:
  prepare:
    uses: splitintech/onedeploy/.github/workflows/autodeploy.yml@REVIEWED_FULL_COMMIT_SHA
    with:
      deployment-branch: deployment
      tooling-ref: REVIEWED_FULL_COMMIT_SHA
      config: deploy/autodeploy.json
      dockerfile: deploy/Dockerfile
      test-script: deploy/test.sh
      model-digest: REVIEWED_MODEL_MANIFEST_SHA256
      image: ghcr.io/your-owner/your-worker
```

The reviewed test script and Dockerfile execute code. Do not enable this on untrusted branches or grant deployment secrets to PR workflows. An integration must separately verify build provenance, qualified controller policy and the exact artifact before any paid rollout. GitHub artifacts are evidence, not durable controller state or an automatic approval.

## Build and verification style

Like Ozzy, the build separates lightweight tooling from inference dependencies, creates immutable artifacts and records explicit qualification gaps:

```sh
python deploy/build_sources.py --output /tmp/onedeploy-release-artifacts
```

Use an empty output directory. This produces wheel/sdist, SHA-256 checksums, MIT notice and a CycloneDX inventory of the pinned development dependencies. This is **not** a complete GPU-image SBOM, license audit or signed provenance attestation. Inference images require their own inspection and model-license review.

See [operations](AUTODEPLOY_OPERATIONS.md), [acceptance](AUTODEPLOY_ACCEPTANCE.md), and the [full design](AUTODEPLOY_PLAN.md).

## Pool and cost planning

`onedeploy.autodeploy.pool` provides deterministic `decide(workers, queued_jobs, budget_remaining, policy)` and `compare_costs(...)` functions. They plan admissions, bounded creation, idle/TTL draining and per-turn versus pooled resource cost using caller-supplied measurements and the **total** hourly resource rate. Busy workers are never proposed for deletion. New capacity is not assumed until deletions are confirmed.

These are advisory decisions, not background jobs or reservations. A trusted persistent controller must atomically reserve budget/capacity, revalidate worker state, execute decisions, monitor deadlines and observe real billing. A cost estimate is not provider pricing or an execution approval.
