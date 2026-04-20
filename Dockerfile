FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libchromaprint-tools \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

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
