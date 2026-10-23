# Troubleshooting without unsafe retries

| Symptom | Safe next action |
| --- | --- |
| Invalid YAML/schema or duplicate variable | Correct the named field/key locally; do not paste secret values into reports. Unknown fields are rejected, not ignored. |
| Env file permission/symlink rejection | Use an explicitly selected regular private file; on POSIX make it mode 0600. Do not loosen protection to bypass validation. |
| Provider command/version missing | Install the documented official version yourself, then run doctor. Onedeploy does not log in or persist credentials automatically. |
| Wrong account/resource or changed remote configuration | Verify the intended destination, inspect again and generate a new plan. Do not edit the stored plan ID. |
| Source/config changed | Review the new bytes/variables and regenerate the plan. Git revision alone is not the source bundle. |
| Snapshot name already exists | Inspect the existing object and any unfinished journal. Use a genuinely new reviewed name only for a new operation, not to bypass an uncertain create. |
| Missing/conflicting volume or unsupported single-writer mode | Use a correct explicitly existing supported resource or redesign the application's cache ownership. Do not silently substitute ephemeral/shared storage. |
| Build failed or command cancelled | Inspect the journal and provider state. Snapshot/image/storage charges can persist. Cancellation is not deletion or rollback. |
| Uncertain write or unresolved same-target operation | Use resume to obtain fresh evidence. Never repeat deployment under a new plan/name to evade the block. Operator outcome assertions must be explicit and grounded in evidence. |
| Health passed, readiness failed | Check model warmup/dependencies and bounded logs. Do not turn a liveness response into application readiness. |
| Protected endpoint returns 401/403 | Expected for credential-free probes. Configure explicitly authorized read-only authenticated checks if supported; never weaken authentication just to get a green probe. |
| Rollback unavailable | Check known local release artifacts and provider limitations. Create the separately reviewed compatible recovery plan; never assume application rollback reverses data/schema changes. |
| Log output appears incomplete | Follow polls snapshots with duplicate suppression; it is not lossless streaming. Use provider tools explicitly if a complete audit log is needed. |

Keep logs private: application output may contain secrets or personal data despite best-effort redaction. Bug reports should include package/provider versions, command name, stable error code and a synthetic minimal manifest, never real environment files, signed URLs or account tokens.
