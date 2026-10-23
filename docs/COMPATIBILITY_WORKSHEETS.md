# Reference application compatibility worksheets

These are sanitized architecture worksheets, not deployable application manifests. They summarize the initial read-only assessment recorded in the approved plan. Exact private revisions, paths, identifiers, environment names and endpoints belong in the owner's private release evidence. No private application code or configuration is bundled here. The implementation's generic templates are independently written.

## OSM Map Studio

- Scope: containerized map frontend/API, not an arbitrary map renderer or public data publication task.
- Evidence: existing container and configuration preflight/provisioning foundations were inspected at an identified private source revision. No live deployment or OAuth session was tested.
- Build/start/port: use the application's reviewed Docker build context, artifact-owned startup and declared application port. Exact values require the private application owner's manifest; this worksheet intentionally does not guess them.
- Variables: classify public frontend origin/config separately from server secrets and provider control credentials. Supply only declared application runtime values, never the operator's full environment.
- Dependencies and persistence: database/schema and storage/bucket policy must exist and be validated separately. Tool deployment does not authorize migrations or policy changes.
- Ingress/auth: browser OAuth requires a stable approved origin and redirects. An expiring sandbox URL is not automatically suitable. Owner login and protected API access require explicit acceptance evidence.
- Readiness: process and HTTP readiness can prove the server responds; they do not prove OAuth, migrations, bucket permissions or user access.
- Safe acceptance: declared read-only health route; authenticated owner/profile read only after explicit credential-transmission approval. No data creation or publication as a hidden smoke test.
- Lifecycle/rollback: known compatible application artifact/config can be redeployed where supported. Database/schema/storage changes cannot be undone by application rollback.
- Provider status: Daytona is an integration candidate based on prior operator workflow; Railway requires new container integration and stable-origin qualification; Modal requires application adaptation and is not a first-release compatibility claim.

## CPU Voice-to-Prompt

- Scope: CPU speech/model service, explicitly not the separate Legal/OpenAI realtime voice implementation.
- Evidence: immutable-image lifecycle, private ingress, model-cache and external dependency foundations were inspected. No audio, model download, latency or inference accuracy was tested.
- Build/start/port: reviewed model-serving image or project Docker build, artifact-owned startup and configured service port. Do not silently substitute a different model or runtime profile.
- Variables: application database/Redis references, model configuration and private BFF authentication are separate from provider credentials. Credentials stay in local explicit sources and approved runtime transport.
- Dependencies: externally managed PostgreSQL and Redis require TLS and application-specific connectivity qualification. A schema declaration is not a database provisioning or migration operation.
- Persistence: dedicated model cache, ownership and writer semantics matter. Shared read/write mounts without exclusive-writer guarantees must not be accepted when the application requires single-writer access. Provider support must be checked before deployment.
- Ingress/auth: private access through the intended BFF is required. Public HTTP readiness is not equivalent to end-user authentication.
- Readiness: process up, model loaded and warmup complete are distinct. Real memory, cache persistence, concurrency, latency and accuracy remain unrun qualification gates.
- Safe acceptance: harmless read-only model/readiness metadata checks. Real audio inference and any user data transmission need a separately authorized test.
- Lifecycle/rollback: redeploy a known compatible image/config only; do not delete caches or assume model/schema versions are interchangeable. Explicit starts do not authorize request-time paid auto-wake.
- Provider status: Daytona is an integration candidate with cache/lifecycle constraints. Railway needs persistent-cache/resource/auth qualification. Modal needs distinct image/function, concurrency, cold-start and persistent-data design.

## Slybroadcast MCP

- Scope: current private HTTP MCP service, distinct from an older separate Modal prototype.
- Evidence: exact-revision Daytona startup, authentication/discovery and expiring endpoint behavior were assessed from source. No messages or campaigns were sent; no live client reconnection was tested.
- Build/start/port: reviewed current service Docker or ASGI package with its artifact-owned startup/declared port. An older prototype is not an interchangeable artifact.
- Variables: upstream application credentials and MCP authentication remain application secrets, separate from provider control-plane tokens. Do not embed signed URLs or credentials in plans/logs.
- Dependencies/persistence: upstream service account and any state are external prerequisites; no automatic account creation or data migration is implied. Confirm application storage requirements before asserting statelessness.
- Ingress/auth: sandbox-signed endpoint expiry and client reconnection are explicit lifecycle concerns. Stable hosting requires distinct packaging/qualification rather than renaming an expiring URL.
- Readiness: HTTP health and declared read-only authentication checks do not establish message-delivery capability or authorize side effects.
- Safe acceptance: health/authentication and explicitly read-only discovery. MCP initialization may need a separate application-specific protocol harness; the generic HTTP checker intentionally does not issue arbitrary POST/RPC calls. No sends/campaigns as deployment tests.
- Lifecycle/rollback: restore a known compatible service artifact/config where supported; never replay messages, revoke unknown credentials or delete application state automatically.
- Provider status: Daytona is the source-level integration candidate. Railway requires stable HTTP packaging/qualification. Modal requires reconciling the older prototype with current behavior, not a blanket compatibility claim.

## Evidence needed to promote a profile

Create the owner's real manifest from a reviewed source revision, fill exact resource identity and secret references locally, validate provider/storage/build capabilities, and record an authorized staging run with artifact identity and layered results. None of these worksheets certifies the three private applications as Onedeploy-ready or production-ready.
