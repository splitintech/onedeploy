# AutoDeploy engineering preview

OneDeploy adds a provider-neutral GitHub release interface with a first concrete **Daytona GPU/Whisper profile**. Code now covers source-bound plans, guarded rollout, an authenticated persistent controller, worker leases, scale-to-zero, conservative spend reservations and an executable CUDA worker. **Live image build, GPU behavior and production cutover have not been qualified.** Other vendors are capability/roadmap entries, not working AutoDeploy adapters merely because they have a CLI.

## 1. Install an isolated reviewed checkout

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

The library supports Python 3.11–3.13; the supplied worker image targets Python 3.12. Install `daytona==0.231.0` **only in this separate AutoDeploy controller environment** before using live commands. Do not silently upgrade the existing legacy Daytona 0.226.0 environment. Installation and imports do not authenticate or provision resources. This is a reviewed source checkout, not a claim of a new published package.

## 2. Check provider capabilities first

```sh
onedeploy autodeploy capabilities --json
onedeploy autodeploy capabilities --provider daytona --json
```

Capability negotiation distinguishes native Git deployment, orchestrated adapters, workspaces and held/unverified products. Prefer a verified native integration and select exactly one trigger owner; never enable a second webhook that duplicates deployment. The [20-provider expansion plan](PROVIDER_EXPANSION_PLAN.md) records prerequisites and qualification gates. None is marked live-qualified by offline tests.

## 3. Prepare the model and reviewed worker image

Use [the supplied worker](../examples/whisper-worker/README.md). It loads local CUDA faster-whisper weights only; no per-request model downloads and no OpenAI hosted `whisper-1` API assumption. Identify and license-review the intended model revision first.

Create an existing Daytona model volume containing a versioned prefix such as:

```text
whisper/revision-123/
  model/                 # complete local faster-whisper model files
  manifest.json          # SHA-256 mapping of every file inside model/
  qualification.wav      # synthetic/authorized fixed test audio
```

The manifest and fixture are outside `model/` to avoid self-referential checksums. The model directory must contain exactly the files listed in the manifest. Record the manifest file's SHA-256 in the release plan. Populate the volume once through an explicitly authorized staging process; this implementation does not silently create a volume or download model assets. All workers mount that versioned prefix and verify it. Each GPU still loads its own RAM/VRAM. The volume API does not expose a read-only mount flag here: immutability is an operating policy, not an invented provider guarantee.

Build the worker from `examples/whisper-worker/Dockerfile`, which pins the verified official CUDA base metadata and hash-locks Python packages. It preserves the base entrypoint and starts the server through Daytona's authenticated process session after mounting inputs. Publish the resulting image to an approved registry and use its digest, not a mutable tag. Registry metadata inspection is not a successful image build or proof of CUDA/toolbox compatibility.

## 4. Review deployment, runtime and budget policy

```sh
onedeploy autodeploy init --output /tmp/autodeploy.json --json
```

Replace every placeholder. Configuration binds repository, branch, account (`target`), deployment ID, explicit CPU/RAM/disk/GPU settings, maximum workers and budget. The template is syntactically valid but not provider-verified. A maximum of one worker cannot overlap an old worker and a rollout candidate; select an explicitly approved limit that supports the desired availability.

Runtime JSON must contain exactly:

```json
{
  "schema_version": 1,
  "model_volume_id": "EXISTING_VOLUME_ID",
  "model_subpath": "whisper/revision-123",
  "model_directory": "model",
  "model_manifest": "manifest.json",
  "synthetic_audio": "qualification.wav",
  "synthetic_text": "The exact expected synthetic transcript."
}
```

Budget policy binds both normalized configuration and runtime digests. Fields are `schema_version: 1`, `config_digest`, `runtime_digest`, `concurrency_per_worker: 1`, `job_timeout_seconds`, `total_hourly_rate`, `max_worker_seconds`, `allocation_overhead_usd` and `max_total_usd`. Obtain current **all-in** CPU/RAM/disk/GPU rates; do not substitute GPU-only pricing. Runtime digest binds volume, model paths and qualification settings. `max_worker_seconds` must use whole minutes, cover startup plus a job and be at most 24 hours; it becomes Daytona's hard wall-clock TTL. `max_total_usd` cannot exceed the deployment budget.

Compute normalized digests with `DeploymentConfig.from_dict(...).to_dict()`, `RuntimeConfig.from_dict(...).to_dict()` and `onedeploy.autodeploy.models.digest`. No secrets belong in these files.

```sh
onedeploy autodeploy setup --config /tmp/autodeploy.json \
  --runtime /secure/runtime.json --policy /secure/policy.json --json
```

Setup is offline validation and prints the exact policy digest to review. It does not establish authorization, GPU availability or provider pricing. The ledger reserves full worker lifetime plus overhead per allocation and never automatically refunds uncertain spend. It enforces approved active slots across rollout candidates and pool workers. Estimates cannot override actual provider prices or replace provider billing alerts.

## 5. Create an immutable source-bound plan

```sh
onedeploy autodeploy plan \
  --config /tmp/autodeploy.json --source /path/to/application \
  --commit FULL_40_CHARACTER_COMMIT_SHA \
  --image ghcr.io/owner/application@sha256:FULL_64_CHARACTER_DIGEST \
  --model-digest MODEL_MANIFEST_SHA256 --evidence-digest TEST_EVIDENCE_SHA256 \
  --out /tmp/rollout-plan.json --json
```

Uppercase markers must be replaced. Source must be clean, including untracked files, and match configured GitHub origin, branch and exact commit. Keep output outside source or under an ignored directory. Strict bounded JSON rejects unknown/duplicate fields; output files are created exclusively. Hashes identify source/evidence but do not prove test success or permission to deploy.

## 6. Explicitly authorize and apply

Live commands make real API calls and can incur costs. Configure `DAYTONA_API_KEY` through approved secret storage in the isolated environment, never command arguments or committed files. After reviewing the plan and policy:

```sh
onedeploy autodeploy apply --plan /tmp/rollout-plan.json \
  --source /path/to/application --approve-plan EXACT_PLAN_ID \
  --state /durable/controller.sqlite --runtime /secure/runtime.json \
  --policy /secure/policy.json --approve-policy EXACT_POLICY_DIGEST --json
```

The engine verifies account identity, creates/reuses the immutable snapshot, mounts existing model inputs, generates a private worker token, starts the fixed worker process, performs actual checksum/CUDA/model/synthetic qualification, atomically promotes the desired release and drains the previous worker. A negative qualification never promotes. Candidate/job timeouts preserve pending intent and immediately request exact-owned dedicated-sandbox termination. Uncertain creation/deletion needs read-only identity/absence reconciliation, never a blind retry or success claim. Every sandbox has a finite hard TTL fallback.

## 7. Start the stable controller

Set `ONEDEPLOY_CONTROLLER_TOKEN` in approved secret storage to a strong value of at least 32 characters. Do not reuse the provider API key. Then run:

```sh
onedeploy autodeploy serve --config /tmp/autodeploy.json --deployment DEPLOYMENT_ID \
  --state /durable/controller.sqlite --runtime /secure/runtime.json \
  --policy /secure/policy.json --approve-policy EXACT_POLICY_DIGEST \
  --host 127.0.0.1 --port 8766
```

The CLI intentionally binds only loopback. A separately configured TLS reverse proxy/private network is required to connect the existing Railway orchestrator remotely; exposing a public unauthenticated port is not part of this feature. Supervise the controller using the operator's normal service manager. No system service is installed automatically.

Authenticated `POST /infer` accepts `{"job_id":"stable-id","audio_base64":"..."}` and returns a durable accepted job. Poll authenticated `GET /jobs/stable-id` for completion. The controller bounds request size/queue/concurrency, stores leases and idempotency records, routes only to the desired release, adopts the qualification candidate rather than creating a duplicate, scales up within limits and deletes idle workers. State and audio/transcript payloads stay on the controller's protected local database until retention removes payloads; tombstone IDs/hashes remain to prevent accidental replay.

Default lease expiry produces an uncertain result rather than silently repeating inference. At-least-once retries require explicit library configuration and may duplicate work across workers after a crash. Exactly-once cloud execution is not promised. Worker hard TTL is the final bound if the controller dies; no paused GPU memory is assumed.

## 8. GitHub branch autodeploy

The reusable `.github/workflows/autodeploy.yml` accepts only a trusted push to the selected branch. It tests exact source, builds a commit-tagged image, resolves its immutable digest and records a rollout plan. Application tests run in a separate checkout to preserve source identity. Tooling is pinned by a reviewed full SHA.

By default `enable-rollout: false`: the workflow stops after publishing the image and plan. To enable paid rollout, configure a persistent self-hosted runner labeled `onedeploy-controller`, a protected GitHub environment with required reviewers/policies, and existing host paths outside the checkout:

- `ONEDEPLOY_STATE_PATH`
- `ONEDEPLOY_RUNTIME_PATH`
- `ONEDEPLOY_POLICY_PATH`
- `ONEDEPLOY_POLICY_ID`
- Environment secret `DAYTONA_API_KEY`

Then explicitly set `enable-rollout: true` and pass the secret to the reusable workflow. The rollout job downloads that run's plan, rechecks exact source and policy, and applies using the durable controller state. It never enables native provider hooks as a side effect. Do not enable this for untrusted PRs; the self-hosted runner executes reviewed tooling only. A failed/unknown apply must be reconciled before rerun.

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
      dockerfile: examples/whisper-worker/Dockerfile
      test-script: deploy/test.sh
      model-digest: REVIEWED_MODEL_MANIFEST_SHA256
      image: ghcr.io/your-owner/your-worker
      enable-rollout: false
```

This example still publishes an image if run. Replace markers and review target costs before enabling. The `cuda-base` input optionally supplies a separately reviewed immutable base digest. CPU/native-build, GPU qualification, rollback drills and the Railway forwarding integration remain release gates, not claims made by the source code.

## Operations and library use

`status` reads existing state. `reconcile`, `rollback`, and `drain` take an exact release approval plus the matching runtime/policy; drain is safe cleanup of non-desired workers, not a command to delete the active service. Use `Adapter`, `Reconciler`, `StateStore`, `Controller`, `DaytonaPoolTransport` and `PoolPolicy` from their documented modules for embedding. Capability negotiation and the generic rollout protocol are provider-neutral; do not send a Daytona profile to another vendor.

`python deploy/build_sources.py --output <empty-directory>` builds wheel/sdist, checksums, MIT notices and a development-lock CycloneDX inventory. This is not a complete worker-image SBOM, signed attestation or legal license audit.

See [operations](AUTODEPLOY_OPERATIONS.md), [acceptance](AUTODEPLOY_ACCEPTANCE.md) and [full design](AUTODEPLOY_PLAN.md).
