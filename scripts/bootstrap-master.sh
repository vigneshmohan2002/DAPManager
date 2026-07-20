#!/usr/bin/env bash
# Bring up the master container with MASTER_PUBLIC_URL set so satellites
# can be onboarded without typing the master's address. Detects the host's
# Tailscale HTTPS URL from an existing root Serve route that proxies to the
# loopback DAPManager port, exports the env var, and runs
# 'docker compose up -d' from the current directory.
#
# Override env vars:
#   MASTER_PUBLIC_URL   skip detection, use this URL verbatim
#   MASTER_PORT         local DAPManager port matched in Serve (default 5001)
#   COMPOSE_FILE        compose file to launch          (default docker-compose.yml)
#
# Flags:
#   --print-only / -n   detect + print, skip 'docker compose up'
#
# Requires python3 for JSON parsing — universally available on macOS/Linux.
# Windows hosts: use the PowerShell sibling (scripts/bootstrap-master.ps1).

set -euo pipefail

PRINT_ONLY=0
case "${1:-}" in
    --print-only|-n) PRINT_ONLY=1 ;;
    -h|--help)
        sed -n '1,/^$/p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    "") ;;
    *) echo "[bootstrap-master] unknown arg: $1 (see --help)" >&2; exit 2 ;;
esac

MASTER_PORT="${MASTER_PORT:-5001}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

log() { echo "[bootstrap-master] $*"; }

detect_tailscale_url() {
    command -v tailscale >/dev/null 2>&1 || {
        log "tailscale CLI not on PATH"
        return 1
    }
    local json
    json=$(tailscale serve status --json 2>/dev/null) || {
        log "'tailscale serve status --json' failed"
        return 1
    }
    command -v python3 >/dev/null 2>&1 || {
        log "python3 not on PATH — install it or set MASTER_PUBLIC_URL manually"
        return 1
    }
    local public_url
    public_url=$(printf '%s' "$json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
port = sys.argv[1]
targets = {
    f"http://127.0.0.1:{port}",
    f"http://localhost:{port}",
}
for authority, site in sorted((data.get("Web") or {}).items()):
    root = ((site or {}).get("Handlers") or {}).get("/") or {}
    if str(root.get("Proxy") or "").rstrip("/") in targets:
        print(f"https://{authority}")
        sys.exit(0)
sys.exit(1)
' "$MASTER_PORT") || {
        log "no root Tailscale Serve route proxies to http://127.0.0.1:$MASTER_PORT"
        return 1
    }
    printf '%s\n' "$public_url"
}

if [ -z "${MASTER_PUBLIC_URL:-}" ]; then
    if detected=$(detect_tailscale_url); then
        MASTER_PUBLIC_URL="$detected"
        log "detected MASTER_PUBLIC_URL = $MASTER_PUBLIC_URL"
    fi
fi

if [ -z "${MASTER_PUBLIC_URL:-}" ]; then
    log "MASTER_PUBLIC_URL is unset and Tailscale detection failed."
    log "Configure Tailscale Serve for 127.0.0.1:$MASTER_PORT or set the HTTPS URL manually, e.g.:"
    log "  MASTER_PUBLIC_URL=https://your-host.example.ts.net:PORT $0"
    exit 1
fi

export MASTER_PUBLIC_URL MASTER_PORT

if [ "$PRINT_ONLY" -eq 1 ]; then
    log "print-only mode; skipping docker compose."
    log "MASTER_PUBLIC_URL=$MASTER_PUBLIC_URL"
    exit 0
fi

command -v docker >/dev/null 2>&1 || { log "docker not on PATH"; exit 1; }
docker compose version >/dev/null 2>&1 || { log "docker compose v2 not available"; exit 1; }

[ -f "$COMPOSE_FILE" ] || {
    log "$COMPOSE_FILE missing — run from the repo root or set COMPOSE_FILE=..."
    exit 1
}

log "starting docker compose with MASTER_PUBLIC_URL in env…"
docker compose -f "$COMPOSE_FILE" up -d

log ""
log "master started. Download link for satellites:"
log "  $MASTER_PUBLIC_URL/download/mac"
