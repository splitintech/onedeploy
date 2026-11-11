# Local-model CUDA Whisper worker

This executable example uses faster-whisper/CTranslate2, **not the hosted OpenAI `whisper-1` API**. No model is silently downloaded. Build and GPU qualification have not been run on a live provider.

## Build

The Dockerfile defaults to official PyTorch `2.11.0-cuda12.8-cudnn9-runtime` pinned to digest `sha256:eee11b3b3872a8c838e35ef48f08b2d5def2080902c7f666831310ca1a0ef2be`. Docker Hub manifest and image-config history were inspected read-only on 2026-11-10: Linux AMD64, CUDA 12.8.1, Ubuntu 24.04 and Python 3.12 paths. No layers were pulled or executed. This verifies artifact identity/source metadata, **not** actual CUDA library loading, image build success or Daytona toolbox compatibility. The base declares no ENTRYPOINT and has `/bin/bash` CMD; this example changes CMD to sleep and leaves the entrypoint unset so Daytona can supply startup. Live qualification remains required. The artifact pipeline must reject an unpinned override before building:

```sh
docker build --build-arg CUDA_BASE='<approved-image>@sha256:<verified-digest>' \
  -f examples/whisper-worker/Dockerfile -t whisper-worker:reviewed .
```

`requirements.lock` pins all 24 resolved dependencies with artifact SHA-256 hashes for Linux x86_64 CPython 3.12. Re-resolve for other architectures/Python versions rather than weakening hashes. The image preserves its base entrypoint for Daytona toolbox compatibility. Controller bootstrap starts `python3 /opt/onedeploy/launch.py` in an authenticated SDK process session after mounting files.

## Required runtime files and settings

- `ONEDEPLOY_MODEL_DIR`: existing model directory, read-only for the worker.
- `ONEDEPLOY_MODEL_MANIFEST`: JSON object mapping every relative model file to its lowercase SHA-256. Store manifest outside model directory. All files must be listed; unsafe paths/symlinks and corruption fail startup.
- `ONEDEPLOY_MODEL_DIGEST`: SHA-256 of exact manifest bytes.
- `ONEDEPLOY_RELEASE_ID`: exact release identity.
- `ONEDEPLOY_GPU_TYPE`: expected model such as `h100`; checked against actual NVIDIA hardware before startup.
- `ONEDEPLOY_WORKER_TOKEN_FILE`: private file containing random bearer secret of at least 32 characters. Never pass the secret itself in CLI arguments.
- `ONEDEPLOY_SYNTHETIC_AUDIO`, `ONEDEPLOY_SYNTHETIC_TEXT`: approved synthetic test audio file and expected transcript; normalized exact match required for qualification. Do not use production audio.
- Optional `ONEDEPLOY_STATE_DIR` (default `/var/lib/onedeploy`) and `ONEDEPLOY_WORKER_PORT` (8766).

Stage model weights once with an approved external download/build process and verify licensing. Multiple workers may mount the immutable versioned directory; they each load their own VRAM. The implementation verifies all checksums before initializing the local-files-only CUDA engine. It rejects a missing GPU or CPU-loaded model; no implicit CPU fallback.

## API and control

All endpoints except minimal `/health` require bearer authentication. POST `/infer` accepts `{ "job_id": "unique-id", "audio_base64": "..." }`; response is `{ "job_id": "...", "text": "...", "release_id": "..." }`. Decoded audio is bounded to 10 MiB and removed after inference. One job at a time per worker; duplicate completed IDs return the original result, conflicting input is rejected, interrupted jobs require controller reconciliation.

POST `/qualify` performs real synthetic inference and persists evidence. GET `/qualification-status` reads that evidence without rerunning inference. POST `/drain` atomically disables admissions. GET `/status` reads active-job counts. Fixed `worker-control qualify|drain|qualify-status|drain-status --json` uses authenticated localhost HTTP. `worker-control infer --input /tmp/od-job-UUID.json --json` reads the bounded uploaded payload; no audio or secret is placed in process arguments.

SQLite records job state, input hashes and completed transcript results for idempotency. This is sensitive runtime data: keep the state directory private; worker deletion removes local state. If mounting durable state, configure external retention/deletion and encryption policies before use. Single-process flock protects restart recovery; prior active jobs become interrupted, not automatically repeated. The external controller remains authoritative across worker deletion. A killed GPU job must be reconciled, not presumed completed.

Provider hard TTL protects bounded lifetime but can interrupt a long request. Admission needs enough remaining TTL for the configured job deadline. Live GPU memory, cold-start, latency, output quality, model volume throughput and end-to-end routing require paid qualification before production.


## CUDA library-loader gate

`launch.py` discovers existing `nvidia/cublas/lib` and `nvidia/cudnn/lib` directories using the running interpreter's site/sysconfig locations plus the packaged dependency directory. It prepends these to the inherited system CUDA search paths and **execs a fresh interpreter** before importing CTranslate2. This supports pip-provided NVIDIA libraries without assuming a Conda prefix or fixed Python installation path; it never downloads libraries. System-wide libraries remain supported.

The Docker build runs `python3 /opt/onedeploy/launch.py --smoke` as the runtime user. It must load the CUDA12 cuBLAS/cuBLASLt and cuDNN9 shared-library SONAMEs and import the real CTranslate2/faster-whisper runtime. A missing/incompatible library fails the build. This CPU-only loader gate does not test a GPU, model loading, correctness, latency or Daytona bootstrap. For an already built image, run the same command without GPU flags to verify the loader gate, then perform separately authorized GPU qualification.

Decoded audio is capped at **120 seconds** (mono 16 kHz, at most 1,920,000 output samples). PyAV iterates packets/frames, checks cumulative decoded duration before resampling/array accumulation, and rejects overlong or unsupported input before GPU inference. A small compressed file cannot bypass the duration limit. Input must contain exactly one audio stream; 8–192 kHz input and up to eight channels are accepted and resampled to mono. Empty audio is rejected.

HTTP/SDK request timeouts are **observation limits, not GPU cancellation**. If a client times out, the worker may still finish inference; its job remains active until actual completion/failure. Do not retry as a new job or delete an active worker to infer success. Reconcile the original job and drain, or wait for the approved hard provider TTL; a TTL may interrupt work and must never be reported as successful completion.
