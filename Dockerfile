FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libchromaprint-tools \
        ca-certificates \
        curl \
        unzip \
        tini \
    && rm -rf /var/lib/apt/lists/*

# sldl (slsk-batchdl) — pinned self-contained release from upstream.
# Bump with --build-arg SLDL_VERSION=vX.Y.Z. Supported arches: amd64, arm64.
ARG SLDL_VERSION=v2.6.0
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) asset="sldl_linux-x64.zip" ;; \
        arm64) asset="sldl_linux-arm.zip" ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/sldl.zip \
        "https://github.com/fiso64/sldl/releases/download/${SLDL_VERSION}/${asset}"; \
    mkdir -p /tmp/sldl; unzip /tmp/sldl.zip -d /tmp/sldl; \
    install -m 0755 /tmp/sldl/sldl /usr/local/bin/sldl; \
    rm -rf /tmp/sldl /tmp/sldl.zip

WORKDIR /app

COPY requirements-server.txt ./
RUN pip install -r requirements-server.txt

COPY src ./src
COPY web ./web
COPY web_server.py ./
COPY scripts/docker-entrypoint.sh /usr/local/bin/dapmanager-entrypoint
RUN chmod +x /usr/local/bin/dapmanager-entrypoint

VOLUME ["/config", "/data"]
WORKDIR /config
EXPOSE 5001

ENTRYPOINT ["tini", "--", "dapmanager-entrypoint"]
CMD ["python", "/app/web_server.py"]
