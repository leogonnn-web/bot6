# ──────────────────────────────────────────────────────
# HYDRA Trading Bot — Python (multi-stage slim build)
# ──────────────────────────────────────────────────────
FROM python:3.11-slim AS base

WORKDIR /app

# System deps for ccxt / websockets
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ──
FROM python:3.11-slim

WORKDIR /app

COPY --from=base /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=base /usr/local/bin /usr/local/bin

# Copy source
COPY src/ ./src/
COPY shared/ ./shared/
COPY main.py .
COPY pytest.ini .

# Health check: hit Prometheus metrics endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/metrics')" || exit 1

# Expose Prometheus metrics
EXPOSE 9090

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src:/app/shared

CMD ["python", "main.py"]
