# Daytona GPU worker contract

AutoDeploy supplies resource lifecycle and a durable desired-release pointer. Your existing authenticated service/router must consume that pointer and hold job leases. Installing the library is **not** a working inference service or automatically connected router.

## Reviewed image requirements

Publish a Linux AMD64 CUDA-compatible image pinned by registry digest. Preserve Daytona toolbox startup; do not substitute an incompatible PID 1. Run the application as a non-root user. Pin dependencies/model revision and check model files with a committed SHA-256 manifest. Do not bake credentials or production audio into an image. CPU/RAM/disk/GPU allocations must be explicit and benchmarked; no hardware minimum is implied here.

Supply executable `/opt/onedeploy/worker-control`. It accepts exactly `qualify --json` and `drain --json`. AutoDeploy calls this fixed path through the authenticated Daytona process API. No arbitrary config command or Python module is imported.

`qualify --json` exits zero and returns this shape only when **actual** model checks pass:

```json
{"gpu_type":"h100","authenticated":true,"cuda_available":true,"model_on_gpu":true,"checksum_verified":true,"synthetic_inference_passed":true,"release_id":"CURRENT_RELEASE_ID","model_digest":"EXACT_MODEL_MANIFEST_SHA256"}
```

Implement these checks against the running inference process, not hardcoded booleans. Perform bounded synthetic inference; no user audio in CI. Verify actual CUDA placement, expected model revision/hash and request authentication. Print no credentials, audio or transcript to stdout.

`drain --json` must atomically disable new admissions, then read authoritative in-flight job leases:

```json
{"accepting_jobs":false,"active_jobs":0}
```

A nonzero active count blocks deletion. The controller calls drain again immediately before deletion. Your router must respect admissions/leases so jobs cannot arrive after the zero count. Maintain in-flight job state durably if needed. A GPU idle stop must not terminate active jobs; qualify lifecycle handling before production.

## SDK integration

Use a **separate** Python environment with `daytona==0.231.0`; do not silently replace OneDeploy's legacy `daytona==0.226.0` installation. `sdk_adapter` accepts an explicit key (never a CLI flag), verifies account identity, and uses the inspected 0.231.0 snapshot resources model. Snapshot creation owns resource settings; snapshot-based sandbox parameters do not accept a resources field. Revalidate future SDK changes with contract tests.

```python
from onedeploy.autodeploy.daytona import sdk_adapter, worker_control
# Obtain api_key from approved secret storage, not source or command arguments.
adapter = sdk_adapter(
    api_key=api_key, account=organization_id, deployment="my-deployment",
    resources={"cpu":2, "memory":8, "disk":20, "gpu":1, "gpu_type":"h100"},
    probe=lambda worker: worker_control(worker, "qualify"),
    drainer=lambda worker: worker_control(worker, "drain"),
    authorize_operation=trusted_budget_and_deployment_policy,
)
```

This is a configuration example, not a price/performance recommendation. Account quotas, available GPU enum values, model memory and budgets must be checked. Creation/deletion calls incur charges and require authorization. Offline tests use an injected client; live GPU performance and routing qualification remain separate acceptance gates. No default model or complete GPU worker is supplied in this example.

The mandatory policy callback receives the full release and must enforce account/source approval, remaining cost budget, maximum workers and bounded resource TTLs. The adapter does not guess billing rates or provide a billing kill-switch. Missing policy blocks live factory construction. This callback must be trusted local application code, never a dynamically imported config module.

## Recovery and exhausted budgets

A known negative GPU/model/inference result returns explicit false qualification flags and rejects that release. Malformed responses and transport failures remain uncertain. Optionally supply `inspect_status(worker, operation)` to the adapter/factory: it must be a **separate read-only** query returning `{"completed":true,"evidence":...}` for the exact current release's persisted qualification or drain operation. The adapter never reruns synthetic inference or a mutating drain command during inspection. Without trustworthy recorded status, recovery remains held for operator reconciliation.

The admission/budget callback gates snapshot creation, candidate creation and qualification. It does **not** block read-only reconciliation or ownership-verified drain/deletion when the budget is exhausted. Ownership, account, approved-resource and active-job checks still apply to cleanup; exhausted funds are not permission to delete unrelated workers.
