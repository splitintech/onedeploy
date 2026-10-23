FROM python:3.12-slim
WORKDIR /app
COPY worker.py /app/worker.py
USER 65534:65534
CMD ["python", "/app/worker.py"]
