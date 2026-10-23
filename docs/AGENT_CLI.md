# Agent-facing CLI contract

Onedeploy requires no agent-specific SDK or LLM provider. A local agent invokes the same executable as a person, using argument arrays and `--json`.

A terminal success envelope has `schema_version`, `command`, `status`, `result`, `error`, `warnings`, and `next_actions`. Use the checked-in JSON Schema. Progress events go to stderr and carry a sequence, timestamp and allowlisted operational identifiers. Ordinary commands emit one final result on stdout. `logs --follow` emits a sequence of result envelopes as newline-delimited JSON; it polls bounded snapshots and suppresses identical consecutive snapshots rather than claiming a lossless incremental stream. Do not treat stderr text as approval.

Use `onedeploy schema --json` for supported manifest shape. `config validate` and local `plan` are non-mutating. `plan --inspect-remote` explicitly contacts the provider read-only using existing local authentication. `deploy --approve-plan ID` can create resources, transmit selected runtime variables and incur costs.

**An approval ID is not an authorization credential.** An agent reading the plan cannot approve itself merely by copying that ID. The embedding agent/system must enforce its own trusted user permissions. Onedeploy is local tooling, not an access-control server.

Stable error codes and exit statuses are defined in `onedeploy/errors.py`; examples include invalid configuration, unsupported capability, approval required, authentication failure, provider failure, uncertain outcome and cancellation. Always inspect `uncertain` and `mutation_possible` before deciding what to do next. Never retry a mutation because a process exited nonzero.

A `success` envelope for status/reconciliation means the observation command completed, not that a deployment succeeded. Check `local_state`, observed provider state and verification layers. `provider_accepted`, `process_running`, `readiness_passed`, `ingress_verified` and `application_qualified` are distinct.

Never pass secrets as flags, paste them into prompts, or store them in plan JSON. Use an explicit private env file or existing selected environment variables. Logs may contain application data; request them explicitly, keep them private and do not forward them automatically.

Versioned schemas can be retrieved using `schema --kind manifest|result|progress|error --json`. Store the schema version with client integrations. Do not infer the shape of every command-specific result from a generic success envelope; check the command, result keys and versioned documentation.

Application checks have a separate `application_checks_passed` result. A declared protected route passing both negative and authenticated checks can provide route-specific ingress evidence; `application_qualified` remains a distinct operator qualification, not an automatic production badge.
