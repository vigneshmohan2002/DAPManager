#!/usr/bin/env bash
# One-shot wiring: register Lidarr as an "application" inside Prowlarr so
# Prowlarr auto-syncs indexers into Lidarr. Idempotent — safe to re-run.
#
# Run after `docker compose --profile arr up -d` has started prowlarr +
# lidarr. Both containers must have booted at least once so their API
# keys are written to config.xml.
#
# Every run writes a timestamped log to ./logs/bootstrap-arr-*.log so
# failures can be diffed / pasted into issues without re-running.
#
# Override via env if you aren't using the defaults:
#   PROWLARR_URL          how THIS script reaches Prowlarr    (default: http://localhost:9696)
#   LIDARR_URL            how THIS script reaches Lidarr      (default: http://localhost:8686)
#   PROWLARR_INTERNAL_URL URL Prowlarr uses to reach itself   (default: http://prowlarr:9696)
#   LIDARR_INTERNAL_URL   URL Prowlarr uses to reach Lidarr   (default: http://lidarr:8686)
#   PROWLARR_CONFIG_DIR   host path of Prowlarr's /config     (default: ./prowlarr-config)
#   LIDARR_CONFIG_DIR     host path of Lidarr's /config       (default: ./lidarr-config)
#   LOG_DIR               where to write the log file         (default: ./logs)

set -euo pipefail

PROWLARR_URL="${PROWLARR_URL:-http://localhost:9696}"
LIDARR_URL="${LIDARR_URL:-http://localhost:8686}"
PROWLARR_INTERNAL_URL="${PROWLARR_INTERNAL_URL:-http://prowlarr:9696}"
LIDARR_INTERNAL_URL="${LIDARR_INTERNAL_URL:-http://lidarr:8686}"
PROWLARR_CONFIG_DIR="${PROWLARR_CONFIG_DIR:-./prowlarr-config}"
LIDARR_CONFIG_DIR="${LIDARR_CONFIG_DIR:-./lidarr-config}"
LOG_DIR="${LOG_DIR:-./logs}"

command -v curl >/dev/null || { echo "[bootstrap-arr] curl required" >&2; exit 1; }
command -v jq   >/dev/null || { echo "[bootstrap-arr] jq required (brew install jq / apt install jq)" >&2; exit 1; }

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/bootstrap-arr-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

log "bootstrap-arr starting — log: $LOG_FILE"
log "  PROWLARR_URL          = $PROWLARR_URL"
log "  LIDARR_URL            = $LIDARR_URL"
log "  PROWLARR_INTERNAL_URL = $PROWLARR_INTERNAL_URL"
log "  LIDARR_INTERNAL_URL   = $LIDARR_INTERNAL_URL"
log "  PROWLARR_CONFIG_DIR   = $PROWLARR_CONFIG_DIR"
log "  LIDARR_CONFIG_DIR     = $LIDARR_CONFIG_DIR"

extract_api_key() {
    local dir="$1" cfg="$1/config.xml" tries=60 key=""
    log "reading API key from $cfg…"
    while [ $tries -gt 0 ]; do
        if [ -f "$cfg" ]; then
            key=$(grep -oE '<ApiKey>[^<]+</ApiKey>' "$cfg" | sed -E 's|<ApiKey>([^<]+)</ApiKey>|\1|' || true)
            if [ -n "$key" ]; then
                printf '%s' "$key"
                return 0
            fi
        fi
        sleep 2
        tries=$((tries - 1))
    done
    fail "timed out waiting for $cfg (container not booted, or wrong *_CONFIG_DIR path?)"
}

wait_for_api() {
    local url="$1" tries=60
    log "waiting for $url/ping…"
    while [ $tries -gt 0 ]; do
        if curl -fsS "$url/ping" >/dev/null 2>&1; then
            log "  $url is up."
            return 0
        fi
        sleep 2
        tries=$((tries - 1))
    done
    fail "$url/ping never responded (service unhealthy?)"
}

# http METHOD KEY URL [extra curl args...]
# Prints response body on success; logs status + body and returns 1 on failure.
http() {
    local method="$1" key="$2" url="$3"; shift 3
    log "→ $method $url"
    local tmp status
    tmp=$(mktemp)
    status=$(curl -sS -o "$tmp" -w '%{http_code}' -X "$method" \
        -H "X-Api-Key: $key" \
        -H "Content-Type: application/json" \
        "$@" "$url" || echo "000")
    if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
        log "← HTTP $status"
        log "← body: $(head -c 500 "$tmp")"
        rm -f "$tmp"
        return 1
    fi
    log "← HTTP $status"
    cat "$tmp"
    rm -f "$tmp"
}

PROWLARR_KEY=$(extract_api_key "$PROWLARR_CONFIG_DIR")
LIDARR_KEY=$(extract_api_key "$LIDARR_CONFIG_DIR")

wait_for_api "$PROWLARR_URL"
wait_for_api "$LIDARR_URL"

log "checking existing Prowlarr applications…"
existing=$(http GET "$PROWLARR_KEY" "$PROWLARR_URL/api/v1/applications") \
    || fail "couldn't list Prowlarr applications — API key mismatch?"

if printf '%s' "$existing" | jq -e --arg url "$LIDARR_INTERNAL_URL" '
    .[] | select(.implementation == "Lidarr")
        | .fields[]? | select(.name == "baseUrl" and .value == $url)
' >/dev/null; then
    log "Lidarr already registered at $LIDARR_INTERNAL_URL — nothing to do."
    exit 0
fi

log "registering Lidarr with Prowlarr…"
# syncCategories 3000-3060 = Audio/* (Lidarr's music categories).
payload=$(jq -n \
    --arg baseUrl "$LIDARR_INTERNAL_URL" \
    --arg prowlarrUrl "$PROWLARR_INTERNAL_URL" \
    --arg apiKey "$LIDARR_KEY" \
    '{
      name: "Lidarr",
      syncLevel: "fullSync",
      implementation: "Lidarr",
      implementationName: "Lidarr",
      configContract: "LidarrSettings",
      fields: [
        {name: "prowlarrUrl",    value: $prowlarrUrl},
        {name: "baseUrl",        value: $baseUrl},
        {name: "apiKey",         value: $apiKey},
        {name: "syncCategories", value: [3000, 3010, 3020, 3030, 3040, 3050, 3060]}
      ],
      tags: []
    }')

http POST "$PROWLARR_KEY" "$PROWLARR_URL/api/v1/applications" -d "$payload" >/dev/null \
    || fail "Prowlarr rejected the Lidarr application payload — see body above."

log "done. Open $PROWLARR_URL to add indexers — they'll sync into Lidarr automatically."
