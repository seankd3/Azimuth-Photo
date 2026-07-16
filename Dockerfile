# Azimuth Photo server — always-on library hub for NAS / Docker hosts.
# Base deps only (no torch / AI models). See docs/INSTALL.md.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PHOTOARCHIVE_HOST=0.0.0.0 \
    PHOTOARCHIVE_PORT=8000 \
    PHOTOARCHIVE_HOME=/data \
    PYTHONPATH=/app/web

# OpenCV / RAW decode runtime libs + curl for HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libglib2.0-0 \
        libgomp1 \
        libgl1 \
        libraw23t64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

COPY web/ /app/web/
COPY scripts/server_entry.py /app/scripts/server_entry.py
COPY VERSION /app/VERSION

RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin photoarchive \
    && mkdir -p /photos /data \
    && chown -R photoarchive:photoarchive /app /photos /data

USER photoarchive

VOLUME ["/photos", "/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PHOTOARCHIVE_PORT}/api/dev/status" || exit 1

CMD ["python", "/app/scripts/server_entry.py", "--host", "0.0.0.0", "--port", "8000"]
