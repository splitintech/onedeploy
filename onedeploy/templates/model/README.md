# Harmless CPU model service

This independently written example scores three numbers using fixed public coefficients. It downloads no weights, sends no messages and makes no external service calls. It demonstrates process health, model warmup/readiness, a cache directory and bounded inference concurrency, not speech recognition or a production ML model.

Run `python app.py` locally, then GET `/health` or `/ready`. POST `/score` with JSON `{"values":[1,2,3]}` yields a deterministic score. Request bodies are not logged. This illustrative HTTP server is not a hardened production server; keep the provider sandbox private.

The Daytona manifest builds the reviewed local Dockerfile into a uniquely named snapshot before creating the sandbox. Replace the organization, sandbox and snapshot placeholders. Review and pin the Docker base image digest for reproducible production builds. No external registry credentials are requested by the template.

`MODEL_CACHE` defaults to ephemeral `/tmp/onedeploy-model-cache`. To use an existing Daytona volume, explicitly add a supported `storage` declaration, for example:

```yaml
storage:
  - name: cache
    resource_id: REPLACE_EXISTING_VOLUME_NAME
    mount_path: /cache
    kind: model-cache
    access: read-write
    single_writer: false
```

Set `MODEL_CACHE=/cache` in your private environment file. Only do this with a dedicated cache or application-safe concurrent access. Daytona does not provide an exclusive-writer attachment guarantee in this adapter; a `single_writer: true` request must be rejected, not silently treated as safe. Existing volume ownership and mount identity are inspected before an approved deployment. No volume is created or deleted automatically.

A real model needs separately reviewed weights/license, memory/GPU requirements, cache ownership, authentication, accuracy, latency and durability acceptance. Warmup of this example does not qualify a real model. Provider builds and snapshots can incur costs and persist beyond a cancelled local command.
