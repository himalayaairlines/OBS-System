# Himalaya OBS API (FastAPI + SQLite)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Database and generated encryption key live on the /data volume, so a
    # rebuilt or replaced container keeps every record and the SMTP password.
    OBS_DB_PATH=/data/obs.db \
    OBS_SECRET_KEY_FILE=/data/.obs_secret.key

# A Chinese-capable font for the passenger menu PDF ("Download Menu").
RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as an unprivileged user that owns only /data.
RUN useradd --system --uid 10001 --home /app obs \
    && mkdir -p /data && chown obs:obs /data
USER obs
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# One process: the stock-alert/email worker runs inside it (OBS_EMBEDDED_WORKER=1).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
