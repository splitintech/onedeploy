# Recovery and rollback

A deployment journal records intent before consequential requests. It must not contain secrets. Protect it like other operational metadata and keep it until the provider outcome has been established.

## Uncertain outcomes

If a request times out or the local process is interrupted, the provider may already have accepted it. Stop issuing new mutations. Inspect the exact recorded resource and operation, then use the resume/reconciliation workflow. Never rerun a create simply because no successful response was received.

A repeated status query is not a deployment retry. Conversely, provider start/create/redeploy/rollback actions are mutations even when they appear to be recovery. They require the same target and approval checks as the initial operation.

Local locking prevents concurrent operations using the same local state, not independent operators on different computers. Provider identity and resource state must be checked again immediately before mutation.

## Cancellation

Local cancellation stops subsequent work where possible. It does not imply remote deletion, rollback, credential revocation or a spending cap. Report known created IDs and uncertain operations, and inspect provider state before continuing.

## Rollback

Rollback means a separately planned transition to an identified prior compatible runtime release. It does not undo database migrations, delete storage, replay application side effects or bypass authentication. Not every provider/profile offers the same rollback operation. Unsupported cases must return an explicit error with manual recovery guidance rather than fabricate success.

Preserve durable application state and review schema compatibility before reverting code. Do not assume old code is safe against a new schema.

## Known local artifacts and provider differences

Successful supported releases retain their sanitized source inventory and private integrity metadata under the selected state directory. These are local recovery inputs, not a backup of your database or runtime secrets. Protect the directory, preserve it across upgrades and monitor disk use. Copying or deleting state carelessly can make rollback unavailable; never upload it with the application.

Railway rollback targets a known compatible local deployment record and reuploads its retained reviewed source/configuration as a new deployment. It does not pretend that the CLI can redeploy an arbitrary old upload ID. Current account/service/resource preconditions and old runtime secret binding must still validate.

Daytona rollback targets a known compatible local release with an immutable snapshot receipt and requires an explicit new sandbox name. It creates a replacement; it does not delete or mutate the original sandbox, switch DNS, transfer clients or reclaim storage. Both old and replacement resources may incur costs. Storage-sharing constraints still apply.

Modal rollback targets an explicit known compatible `vN` release using the supported provider operation. Provider acceptance alone does not prove the newly created deployment version's identity/readiness when a unique receipt is unavailable.

Illustrative separate-plan workflow (substitute actual locally inspected IDs):

```sh
onedeploy rollback --deployment CURRENT_ID --to PRIOR_LOCAL_ID --plan-out rollback-plan.json --json
# Daytona additionally requires --replacement-name NEW_REVIEWED_SANDBOX_NAME.
# Modal --to is a reviewed vN version instead of a local deployment ID.
onedeploy deploy --plan rollback-plan.json --approve-plan REVIEWED_PLAN_ID --env-file .env --json
```

Do not approve the resulting plan until the artifact, configuration, compatibility assertion, target, mounts and cost implications are reviewed. A missing archive, changed archive, unknown receipt, unsupported storage transition or incompatible data contract must fail closed.

## Explicit reconciliation

`resume --deployment ID --json` obtains fresh evidence. A safe interrupted no-write case can produce a separately approved recovery plan. Ambiguous writes require operator review; when sufficient external evidence establishes the outcome, the paired `--resolve-with-observation HASH --outcome operator-confirmed-completed|operator-confirmed-not-executed` arguments record that assertion. This is not independent proof or a hidden retry command. An agent needs the trusted external approval boundary to make that assertion.
