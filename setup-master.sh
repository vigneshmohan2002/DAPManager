#!/usr/bin/env bash
# One-command master install: copies the compose template on first run,
# brings up dapmanager + lidarr + prowlarr, then wires Prowlarr → Lidarr.
#
# Soulseek (sldl) is still the primary downloader inside DAPManager. Lidarr
# + Prowlarr are optional sidecars that give you Usenet/torrent indexer
# searches on top.
#
# Every run writes a timestamped log to ./logs/setup-master-*.log.

set -euo pipefail

LOG_DIR="${LOG_DIR:-./logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup-master-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

log "setup-master starting — log: $LOG_FILE"

command -v docker >/dev/null || fail "docker not found on PATH"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 not available"

if [ ! -f docker-compose.yml ]; then
    log "no docker-compose.yml — copying from docker-compose.example.yml"
    cp docker-compose.example.yml docker-compose.yml
else
    log "docker-compose.yml already exists — leaving it alone"
fi

log "starting services (profile: arr) — this may take a minute on first run…"
docker compose --profile arr up -d

log "handing off to scripts/bootstrap-arr.sh (it will wait for services to boot)…"
./scripts/bootstrap-arr.sh

cat <<EOF

[$(date '+%H:%M:%S')] master setup complete.

Next steps (manual, one-time):
  1. http://localhost:5001/setup  — fill in Soulseek creds, Jellyfin, etc.
                                    Set lidarr_url = http://lidarr:8686
                                    Copy lidarr's API key from http://localhost:8686 (Settings → General).
  2. http://localhost:9696        — add indexers in Prowlarr.
                                    They auto-sync into Lidarr.
  3. http://localhost:8686        — set a Root Folder (e.g. /music) in Lidarr.

Full log: $LOG_FILE
EOF
