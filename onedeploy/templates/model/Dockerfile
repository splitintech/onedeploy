# Review and pin the base image digest before production use.
FROM python:3.12-slim
WORKDIR /app
COPY app.py /app/app.py
ENV PORT=8000
EXPOSE 8000
CMD ["python", "app.py"]
