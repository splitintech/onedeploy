# Security

Onedeploy is local developer tooling, not a sandbox for untrusted source. Build files and provider entrypoints execute code. Review source and manifests before use.

Never put credentials in issues, plans, screenshots, commits, build arguments or command arguments. Keep local environment and connection files private. Provider credentials stay in the operator process; only explicitly selected application variables may be transmitted to the chosen provider.

A plan ID binds intent, not identity or human approval. An agent integration must enforce its own trusted authorization policy. Do not give untrusted remote agents unrestricted local deployment access.

For suspected vulnerabilities, use GitHub's private vulnerability reporting if enabled on this repository. If unavailable, contact the repository maintainers privately through an existing verified channel; do not publish secrets or exploit details in an issue. No reporting inbox is claimed by this document.

The initial implementation is experimental and offline-tested. No provider/account/application is production-qualified by installing the package. Logs can contain sensitive user application data; redaction is best-effort and logs are never uploaded automatically.
