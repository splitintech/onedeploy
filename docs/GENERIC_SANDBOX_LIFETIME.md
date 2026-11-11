# Finite lifetime for generic Daytona operations

**Contract migration:** adapter versions are now `3-cli-ttl` and `3-sdk-ttl`. Saved plans from the earlier `2-cli` / `2-sdk` contracts must be recreated and reviewed; they are not silently upgraded.

The generic OneDeploy Daytona CLI and SDK transports require bounded sandbox lifetimes. This includes agents such as Codex invoking the same `onedeploy` commands; OneDeploy does not control an unrelated Codex execution environment.

- New sandboxes receive `ttl_minutes: 15` by default. Set `resources.ttl_minutes` to an integer from 1 to 1440 in the reviewed manifest. Zero, negative, boolean and over-limit values are rejected.
- The provider receives a wall-clock TTL through Daytona 0.226.0's documented `--ttl` / `ttl_minutes` field. This is separate from idle auto-stop. Disabling idle auto-stop does not disable the mandatory TTL.
- New sandboxes use immediate deletion upon stop (`auto_delete_interval=0`), not indefinite stopped retention. Retained snapshots and separately managed volumes are not deleted by this policy.
- Existing sandboxes may be inspected and logged without a lease. Starting them or executing readiness/application verification requires an observed future `autoDestroyAt` / `auto_destroy_at` within 24 hours. An expired or missing lifetime is not reset silently. Create a new reviewed sandbox instead.
- A failed or timed-out create triggers an immediate best-effort delete of only the exact sandbox matching the operation's random ownership label and verified account/name. Post-create identity/info validation failures take the same cleanup path. Another operation's sandbox is never deleted to recover a failure.
- Start or verification failures on pre-existing sandboxes remain uncertain and bounded by their verified TTL. A managed label does not establish exclusive ownership, so these failures do not delete a potentially shared sandbox.

The bridges reserve a bounded cleanup window where practical. A process being forcibly killed, network outage or inaccessible provider can prevent immediate cleanup. The independent provider TTL is the final bound; the tool never claims confirmed deletion merely because it attempted it. Failed writes remain uncertain for inspection/reconciliation. Do not treat the resource TTL as a bound on separate registry, snapshot, storage or network charges.

Official contract references: [CLI create v0.226.0](https://github.com/daytona/clients/blob/v0.226.0/cli/cmd/sandbox/create.go), [sandbox API model](https://github.com/daytona/clients/blob/v0.226.0/api-client-python/daytona_api_client/models/sandbox.py).
