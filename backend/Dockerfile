FROM python:3.12-slim AS builder
WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN /opt/venv/bin/pip install --no-cache-dir --only-binary=:all: -r requirements.txt

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    LITELLM_TELEMETRY=False
WORKDIR /app
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid appuser --no-create-home appuser \
    && mkdir -p /data /home/appuser \
    && chown appuser:appuser /data /home/appuser
COPY --from=builder /opt/venv /opt/venv
COPY app/ ./app/
COPY healthcheck.py .
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --start-period=30s --retries=4 CMD ["python", "healthcheck.py"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
