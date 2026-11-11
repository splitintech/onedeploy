# syntax=docker/dockerfile:1
# Docker Hub manifest/config verified read-only 2026-11-10: CUDA 12.8.1,
# Ubuntu 24.04, Python 3.12 history. Live Daytona/GPU qualification remains required.
ARG CUDA_BASE=pytorch/pytorch:2.11.0-cuda12.8-cudnn9-runtime@sha256:eee11b3b3872a8c838e35ef48f08b2d5def2080902c7f666831310ca1a0ef2be
FROM --platform=linux/amd64 ${CUDA_BASE}
USER root
RUN python3 -c "import sys; assert sys.version_info[:2] == (3, 12)" && python3 -m pip --version
WORKDIR /opt/onedeploy
COPY examples/whisper-worker/requirements.lock ./requirements.lock
RUN python3 -m pip install --require-hashes --no-cache-dir --target /opt/onedeploy/deps -r requirements.lock
COPY examples/whisper-worker/worker.py ./worker.py
COPY examples/whisper-worker/launch.py ./launch.py
COPY examples/whisper-worker/worker-control ./worker-control
RUN chmod 755 worker-control && mkdir -p /var/lib/onedeploy && chown -R 1000:1000 /opt/onedeploy /var/lib/onedeploy
ENV PYTHONPATH=/opt/onedeploy/deps HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONUNBUFFERED=1
USER 1000:1000
# CPU-only loader/import gate: no model loading or GPU certification.
RUN python3 /opt/onedeploy/launch.py --smoke
EXPOSE 8766
# Preserve the approved Daytona-compatible base entrypoint. Launch the worker
# through an authenticated process session after mounting model/state/secrets.
CMD ["sleep", "infinity"]
