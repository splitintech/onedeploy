# Dependency license review

Reviewed 2026-10-23 against the tested development environment and `requirements-all.lock`. This is a bounded engineering compatibility review, not legal advice or a certification of upstream ownership.

Onedeploy preserves the repository's MIT license. It imports dependencies installed separately from their packages; it does not copy their implementation into this repository or relicense them as MIT. The inventory below records declared metadata, with the two Daytona analytics clients checked against their wheel LICENSE because their metadata omitted a license field.

## Compatibility conclusion and redistribution conditions

The inventory contains MIT/BSD/PSF permissive families, Apache-2.0, and MPL-2.0 dependencies. No GPL/AGPL declaration or unexplained unknown license was observed in this tested inventory. These dependencies can be used by this MIT-licensed package while retaining their own terms. This conclusion does not mean all dependencies themselves become MIT.

- Preserve copyright/license notices when redistributing dependency code. Apache dependencies may additionally require NOTICE handling and carry their own patent provisions.
- `certifi` and `bidict` declare MPL-2.0. If redistributing those files or modifications, retain their notices and meet file-level source availability obligations. This project does not modify or vendor them. A future bundled binary/container distribution needs its own complete notices/source-offer review.
- Example Docker base images, user application code, model weights and dynamically added plugins are not covered by this Python dependency inventory. Their licenses must be reviewed for the actual artifact.
- Version updates require regenerating this inventory and rechecking changes. Installed-package declarations are evidence, not proof of the licensing of every transitive native component or optional platform wheel.
- No package-registry release or bundled third-party binary is included in this change.

## Tested inventory

| Distribution | Version | Declared license | Evidence |
| --- | --- | --- | --- |
| aiofiles | 25.1.0 | Apache Software License | Installed distribution metadata |
| aiohappyeyeballs | 2.7.1 | Python Software Foundation License | Installed distribution metadata |
| aiohttp | 3.14.5 | Apache-2.0 AND MIT | Installed distribution metadata |
| aiohttp-retry | 2.9.1 | MIT License | Installed distribution metadata |
| aiosignal | 1.4.0 | Apache Software License | Installed distribution metadata |
| annotated-types | 0.8.0 | MIT | Installed distribution metadata |
| anyio | 4.15.1 | MIT | Installed distribution metadata |
| attrs | 26.1.0 | MIT | Installed distribution metadata |
| bidict | 0.24.1 | MPL-2.0 | Installed distribution metadata |
| build | 1.3.0 | MIT | Installed distribution metadata |
| cbor2 | 6.1.5 | MIT | Installed distribution metadata |
| certifi | 2026.10.19 | Mozilla Public License 2.0 (MPL 2.0) | Installed distribution metadata |
| charset-normalizer | 3.5.2 | MIT | Installed distribution metadata |
| click | 8.5.0 | BSD-3-Clause | Installed distribution metadata |
| daytona | 0.226.0 | Apache-2.0 | Installed distribution metadata |
| daytona_analytics_api_client | 0.226.0 | Apache-2.0 | Installed wheel licenses/LICENSE |
| daytona_analytics_api_client_async | 0.226.0 | Apache-2.0 | Installed wheel licenses/LICENSE |
| daytona_api_client | 0.226.0 | Apache-2.0 | Installed distribution metadata |
| daytona_api_client_async | 0.226.0 | Apache-2.0 | Installed distribution metadata |
| daytona_toolbox_api_client | 0.226.0 | Apache-2.0 | Installed distribution metadata |
| daytona_toolbox_api_client_async | 0.226.0 | Apache-2.0 | Installed distribution metadata |
| Deprecated | 1.3.1 | MIT License | Installed distribution metadata |
| frozenlist | 1.8.0 | Apache-2.0 | Installed distribution metadata |
| googleapis-common-protos | 1.76.0 | Apache-2.0 | Installed distribution metadata |
| grpclib | 0.4.9 | BSD License | Installed distribution metadata |
| h11 | 0.16.0 | MIT License | Installed distribution metadata |
| h2 | 4.4.1 | MIT | Installed distribution metadata |
| hpack | 4.2.0 | MIT | Installed distribution metadata |
| httpcore | 1.0.9 | BSD-3-Clause | Installed distribution metadata |
| httpx | 0.28.1 | BSD License | Installed distribution metadata |
| httpx-ws | 0.9.0 | MIT License | Installed distribution metadata |
| hyperframe | 6.1.0 | MIT License | Installed distribution metadata |
| idna | 3.20 | BSD-3-Clause | Installed distribution metadata |
| iniconfig | 2.3.1 | MIT | Installed distribution metadata |
| jsonschema | 4.25.1 | MIT | Installed distribution metadata |
| jsonschema-specifications | 2025.9.1 | MIT | Installed distribution metadata |
| linkify-it-py | 2.2.0 | MIT License | Installed distribution metadata |
| markdown-it-py | 4.2.0 | MIT License | Installed distribution metadata |
| mdit-py-plugins | 0.6.1 | MIT License | Installed distribution metadata |
| mdurl | 0.1.2 | MIT License | Installed distribution metadata |
| modal | 1.6.2 | Apache-2.0 | Installed distribution metadata |
| multidict | 7.1.0 | Apache-2.0 | Installed distribution metadata |
| obstore | 0.11.1 | MIT License | Installed distribution metadata |
| opentelemetry-api | 1.45.1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-exporter-http-transport | 0.66b1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-exporter-otlp-common | 0.66b1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-exporter-otlp-proto-common | 1.45.1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-exporter-otlp-proto-http | 1.45.1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-instrumentation | 0.66b1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-instrumentation-aiohttp-client | 0.66b1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-proto | 1.45.1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-sdk | 1.45.1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-semantic-conventions | 0.66b1 | Apache-2.0 | Installed distribution metadata |
| opentelemetry-util-http | 0.66b1 | Apache-2.0 | Installed distribution metadata |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | Installed distribution metadata |
| platformdirs | 4.13.5 | MIT | Installed distribution metadata |
| pluggy | 1.7.0 | MIT License | Installed distribution metadata |
| propcache | 0.5.4 | Apache-2.0 | Installed distribution metadata |
| protobuf | 7.36.2 | 3-Clause BSD License | Installed distribution metadata |
| pydantic | 2.14.1 | MIT | Installed distribution metadata |
| pydantic_core | 2.50.1 | MIT | Installed distribution metadata |
| Pygments | 2.21.0 | BSD-2-Clause | Installed distribution metadata |
| pyproject_hooks | 1.3.3 | MIT | Installed distribution metadata |
| pytest | 8.4.2 | MIT License | Installed distribution metadata |
| pytest-asyncio | 1.2.0 | Apache-2.0 | Installed distribution metadata |
| python-dateutil | 3.0.0 | BSD License, Apache Software License | Installed distribution metadata |
| python-dotenv | 1.2.4 | BSD-3-Clause | Installed distribution metadata |
| python-engineio | 4.14.0 | MIT | Installed distribution metadata |
| python-multipart | 0.0.32 | Apache-2.0 | Installed distribution metadata |
| python-socketio | 5.17.0 | MIT | Installed distribution metadata |
| PyYAML | 6.0.3 | MIT License | Installed distribution metadata |
| referencing | 0.37.0 | MIT | Installed distribution metadata |
| requests | 2.34.2 | Apache Software License | Installed distribution metadata |
| rich | 15.0.0 | MIT License | Installed distribution metadata |
| rpds-py | 2026.9.1 | MIT | Installed distribution metadata |
| setuptools | 80.9.0 | MIT | Installed distribution metadata |
| simple-websocket | 1.1.0 | MIT License | Installed distribution metadata |
| six | 1.17.0 | MIT License | Installed distribution metadata |
| synchronicity | 0.12.6 | Apache Software License | Installed distribution metadata |
| textual | 8.2.8 | MIT License | Installed distribution metadata |
| toml | 0.10.2 | MIT License | Installed distribution metadata |
| types-certifi | 2021.10.8.3 | Apache Software License | Installed distribution metadata |
| types-toml | 0.10.8.20260518 | Apache-2.0 | Installed distribution metadata |
| typing-inspection | 0.4.4 | MIT | Installed distribution metadata |
| typing_extensions | 4.16.0 | PSF-2.0 | Installed distribution metadata |
| tzdata | 2026.5 | Apache-2.0 | Installed distribution metadata |
| urllib3 | 2.8.0 | MIT | Installed distribution metadata |
| watchfiles | 1.3.0 | MIT License | Installed distribution metadata |
| websocket-client | 1.9.2 | Apache-2.0 | Installed distribution metadata |
| wheel | 0.45.1 | MIT License | Installed distribution metadata |
| wrapt | 2.5.1 | BSD-2-Clause | Installed distribution metadata |
| wsproto | 1.3.2 | MIT | Installed distribution metadata |
| yarl | 1.25.1 | Apache-2.0 | Installed distribution metadata |
