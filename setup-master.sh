#!/usr/bin/env bash
# One-command master install for Linux / macOS hosts.
# Detects Tailscale URL, starts all containers, wires Prowlarr → Lidarr,
# sets Lidarr root folder, and pushes credentials into DAPManager —
# no browser tabs required for the core setup.
#
# Credential priority (each value):
#   1. Environment variable  (SLSK_USERNAME, SLSK_PASSWORD, JELLYFIN_*)
#   2. Interactive prompt    (Soulseek only — Jellyfin silently skipped if absent)
#
# Override / tune via env:
#   MASTER_PUBLIC_URL   skip Tailscale detection, use this URL
#   MASTER_PORT         port in the composed URL           (default 5001)
#   SLSK_USERNAME       Soulseek username
#   SLSK_PASSWORD       Soulseek password
#   JELLYFIN_URL        Jellyfin server URL                (optional)
#   JELLYFIN_API_KEY    Jellyfin API key                   (optional)
#   JELLYFIN_USER_ID    Jellyfin user ID                   (optional)
#   DAP_URL             how this script reaches DAPManager (default http://localhost:5001)
#   LIDARR_URL          how this script reaches Lidarr     (default http://localhost:8686)
#   PROWLARR_URL        how this script reaches Prowlarr   (default http://localhost:9696)
#   LIDARR_INTERNAL_URL URL Prowlarr uses for Lidarr       (default http://lidarr:8686)
#   PROWLARR_INTERNAL_URL URL Prowlarr uses for itself     (default http://prowlarr:9696)
#   LOG_DIR             where to write timestamped logs    (default ./logs)
#   COMPOSE_FILE        compose file to use                (default docker-compose.yml)
#   SKIP_ARR            set to 1 to start DAPManager only
#
# Windows hosts: use scripts/setup-master.ps1 instead.

set -euo pipefail

MASTER_PORT="${MASTER_PORT:-5001}"
DAP_URL="${DAP_URL:-http://localhost:5001}"
LIDARR_URL="${LIDARR_URL:-http://localhost:8686}"
PROWLARR_URL="${PROWLARR_URL:-http://localhost:9696}"
LIDARR_INTERNAL_URL="${LIDARR_INTERNAL_URL:-http://lidarr:8686}"
PROWLARR_INTERNAL_URL="${PROWLARR_INTERNAL_URL:-http://prowlarr:9696}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
SKIP_ARR="${SKIP_ARR:-0}"
LOG_DIR="${LOG_DIR:-./logs}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/setup-master-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "[$(date '+%H:%M:%S')] [OK] $*"; }
warn() { echo "[$(date '+%H:%M:%S')] [WARN] $*"; }
fail() { echo "[$(date '+%H:%M:%S')] [ERROR] $*" >&2; exit 1; }

log "setup-master starting — log: $LOG_FILE"

# ── Prerequisites ─────────────────────────────────────────────────────────────

command -v docker  >/dev/null || fail "docker not found on PATH"
docker compose version >/dev/null 2>&1 || fail "docker compose v2 not available"
command -v curl    >/dev/null || fail "curl not found on PATH"
command -v python3 >/dev/null || fail "python3 not found on PATH (needed for JSON parsing)"
ok "prerequisites OK"

# ── Tailscale URL detection ───────────────────────────────────────────────────

detect_tailscale_url() {
    command -v tailscale >/dev/null 2>&1 || { warn "tailscale CLI not on PATH"; return 1; }
    local json
    json=$(tailscale status --json 2>/dev/null) || { warn "'tailscale status --json' failed (daemon down?)"; return 1; }
    local host
    host=$(printf '%s' "$json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
self_ = data.get("Self") or {}
dns = (self_.get("DNSName") or "").rstrip(".")
if dns:
    print(dns); sys.exit(0)
for ip in (self_.get("TailscaleIPs") or []):
    if ":" not in ip:
        print(ip); sys.exit(0)
sys.exit(1)
') || { warn "no usable Tailscale identity (MagicDNS off and no IPv4?)"; return 1; }
    printf 'http://%s:%s\n' "$host" "$MASTER_PORT"
}

if [ -z "${MASTER_PUBLIC_URL:-}" ]; then
    if detected=$(detect_tailscale_url); then
        MASTER_PUBLIC_URL="$detected"
        ok "detected MASTER_PUBLIC_URL = $MASTER_PUBLIC_URL"
    else
        warn "MASTER_PUBLIC_URL not detected — satellite distribution unavailable until set in Settings."
        MASTER_PUBLIC_URL=""
    fi
fi
export MASTER_PUBLIC_URL MASTER_PORT

# ── Compose file ──────────────────────────────────────────────────────────────

if [ ! -f "$COMPOSE_FILE" ]; then
    [ -f docker-compose.example.yml ] || fail "docker-compose.example.yml not found — run from the repo root."
    cp docker-compose.example.yml "$COMPOSE_FILE"
    ok "copied docker-compose.example.yml → $COMPOSE_FILE"
else
    log "$COMPOSE_FILE already exists — leaving it alone"
fi

# ── Start containers ──────────────────────────────────────────────────────────

if [ "$SKIP_ARR" = "1" ]; then
    log "starting DAPManager only (SKIP_ARR=1)…"
    docker compose -f "$COMPOSE_FILE" up -d
else
    log "starting services (profile: arr) — first run may take a minute…"
    docker compose -f "$COMPOSE_FILE" --profile arr up -d
fi
ok "containers started"

# ── Wait helpers ──────────────────────────────────────────────────────────────

wait_http() {
    local url="$1" name="$2" timeout="${3:-120}" elapsed=0
    log "waiting for $name at $url…"
    while [ $elapsed -lt $timeout ]; do
        if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
            ok "$name is up"; return 0
        fi
        sleep 2; elapsed=$((elapsed + 2))
    done
    fail "$name did not respond within ${timeout}s ($url)"
}

read_xml_api_key() {
    local cfg="$1" name="$2" timeout="${3:-120}" elapsed=0
    log "waiting for $name API key in $cfg…"
    while [ $elapsed -lt $timeout ]; do
        if [ -f "$cfg" ]; then
            local key
            key=$(grep -oE '<ApiKey>[^<]+</ApiKey>' "$cfg" | sed -E 's|<ApiKey>([^<]+)</ApiKey>|\1|' || true)
            if [ -n "$key" ] && [ "${#key}" -gt 4 ]; then
                ok "read $name API key"; printf '%s' "$key"; return 0
            fi
        fi
        sleep 2; elapsed=$((elapsed + 2))
    done
    fail "timed out waiting for $name API key in $cfg"
}

# ── Health checks ─────────────────────────────────────────────────────────────

wait_http "$DAP_URL/api/healthz" "DAPManager"

if [ "$SKIP_ARR" != "1" ]; then
    wait_http "$LIDARR_URL/api/v1/system/status"  "Lidarr"
    wait_http "$PROWLARR_URL/ping"                 "Prowlarr"
fi

# ── API keys ──────────────────────────────────────────────────────────────────

if [ "$SKIP_ARR" != "1" ]; then
    LIDARR_KEY=$(read_xml_api_key "./lidarr-config/config.xml"   "Lidarr")
    PROWLARR_KEY=$(read_xml_api_key "./prowlarr-config/config.xml" "Prowlarr")

    # ── Prowlarr → Lidarr wiring ──────────────────────────────────────────────

    arr_http() {
        local method="$1" key="$2" url="$3" body="${4:-}"
        local args=(-sS -X "$method" -H "X-Api-Key: $key" -H "Content-Type: application/json")
        [ -n "$body" ] && args+=(-d "$body")
        local tmp status
        tmp=$(mktemp)
        status=$(curl "${args[@]}" -o "$tmp" -w '%{http_code}' "$url" || echo "000")
        if [ "$status" -lt 200 ] || [ "$status" -ge 300 ]; then
            warn "HTTP $status from $url: $(head -c 200 "$tmp")"
            rm -f "$tmp"; return 1
        fi
        cat "$tmp"; rm -f "$tmp"
    }

    log "checking existing Prowlarr application registrations…"
    existing=$(arr_http GET "$PROWLARR_KEY" "$PROWLARR_URL/api/v1/applications" || echo "[]")
    already=$(printf '%s' "$existing" | python3 -c "
import json, sys
apps = json.load(sys.stdin)
internal = '${LIDARR_INTERNAL_URL}'
for a in apps:
    if a.get('implementation') == 'Lidarr':
        for f in a.get('fields', []):
            if f.get('name') == 'baseUrl' and f.get('value') == internal:
                print('yes'); sys.exit(0)
print('no')
" 2>/dev/null || echo "no")

    if [ "$already" = "yes" ]; then
        ok "Lidarr already registered in Prowlarr — skipping"
    else
        log "registering Lidarr with Prowlarr…"
        payload=$(python3 -c "
import json
print(json.dumps({
  'name': 'Lidarr', 'syncLevel': 'fullSync',
  'implementation': 'Lidarr', 'implementationName': 'Lidarr',
  'configContract': 'LidarrSettings',
  'fields': [
    {'name': 'prowlarrUrl',    'value': '${PROWLARR_INTERNAL_URL}'},
    {'name': 'baseUrl',        'value': '${LIDARR_INTERNAL_URL}'},
    {'name': 'apiKey',         'value': '${LIDARR_KEY}'},
    {'name': 'syncCategories', 'value': [3000,3010,3020,3030,3040,3050,3060]}
  ], 'tags': []
}))")
        arr_http POST "$PROWLARR_KEY" "$PROWLARR_URL/api/v1/applications" "$payload" >/dev/null \
            && ok "Prowlarr → Lidarr registered" \
            || warn "Prowlarr application registration failed — check logs and re-run."
    fi

    # ── Lidarr root folder ────────────────────────────────────────────────────

    log "checking Lidarr root folders…"
    root_folders=$(arr_http GET "$LIDARR_KEY" "$LIDARR_URL/api/v1/rootFolder" || echo "[]")
    has_music=$(printf '%s' "$root_folders" | python3 -c "
import json, sys
folders = json.load(sys.stdin)
print('yes' if any(f.get('path') == '/music' for f in folders) else 'no')
" 2>/dev/null || echo "no")

    if [ "$has_music" = "yes" ]; then
        ok "Lidarr root folder /music already set — skipping"
    else
        log "setting Lidarr root folder to /music…"
        arr_http POST "$LIDARR_KEY" "$LIDARR_URL/api/v1/rootFolder" '{"path":"/music"}' >/dev/null \
            && ok "Lidarr root folder /music set" \
            || warn "could not set Lidarr root folder — set it manually at $LIDARR_URL."
    fi
fi

# ── Credentials ───────────────────────────────────────────────────────────────

SLSK_USERNAME="${SLSK_USERNAME:-}"
SLSK_PASSWORD="${SLSK_PASSWORD:-}"

if [ -z "$SLSK_USERNAME" ]; then
    printf "Soulseek username (leave blank to skip): "
    read -r SLSK_USERNAME || SLSK_USERNAME=""
fi
if [ -n "$SLSK_USERNAME" ] && [ -z "$SLSK_PASSWORD" ]; then
    printf "Soulseek password: "
    read -rs SLSK_PASSWORD; echo
fi

# ── Push config to DAPManager ─────────────────────────────────────────────────

log "pushing configuration to DAPManager…"

# Build JSON patch with only the keys that have values
build_patch() {
    python3 -c "
import json, sys
patch = {}
$([ -n "$MASTER_PUBLIC_URL" ] && echo "patch['public_master_url'] = '${MASTER_PUBLIC_URL}'.rstrip('/')")
$([ -n "$SLSK_USERNAME" ]     && echo "patch['slsk_username'] = '${SLSK_USERNAME}'")
$([ -n "$SLSK_PASSWORD" ]     && echo "patch['slsk_password'] = '${SLSK_PASSWORD}'")
$([ -n "${JELLYFIN_URL:-}" ]     && echo "patch['jellyfin_url']     = '${JELLYFIN_URL}'")
$([ -n "${JELLYFIN_API_KEY:-}" ] && echo "patch['jellyfin_api_key'] = '${JELLYFIN_API_KEY}'")
$([ -n "${JELLYFIN_USER_ID:-}" ] && echo "patch['jellyfin_user_id'] = '${JELLYFIN_USER_ID}'")
$([ "$SKIP_ARR" != "1" ]     && echo "patch['lidarr_enabled'] = True")
$([ "$SKIP_ARR" != "1" ]     && echo "patch['lidarr_url']     = '${LIDARR_INTERNAL_URL}'")
$([ "$SKIP_ARR" != "1" ]     && [ -n "${LIDARR_KEY:-}" ] && echo "patch['lidarr_api_key'] = '${LIDARR_KEY}'")
print(json.dumps(patch))
"
}

PATCH=$(build_patch)

# Read api_token from config.json if present
API_TOKEN=""
CONFIG_JSON="./config/config.json"
if [ -f "$CONFIG_JSON" ]; then
    API_TOKEN=$(python3 -c "
import json
with open('${CONFIG_JSON}') as f:
    c = json.load(f)
print(c.get('api_token',''))
" 2>/dev/null || true)
fi

AUTH_HEADER=""
[ -n "$API_TOKEN" ] && AUTH_HEADER="-H 'Authorization: Bearer $API_TOKEN'"

result=$(curl -sS -X POST "$DAP_URL/api/config" \
    -H "Content-Type: application/json" \
    ${API_TOKEN:+-H "Authorization: Bearer $API_TOKEN"} \
    -d "$PATCH" || echo '{"success":false,"message":"curl failed"}')

if printf '%s' "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('success') else 1)" 2>/dev/null; then
    changed=$(printf '%s' "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(', '.join(d.get('changed',[])))" 2>/dev/null || echo "?")
    ok "DAPManager config updated (changed: $changed)"
else
    msg=$(printf '%s' "$result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('message','unknown'))" 2>/dev/null || echo "unknown")
    warn "DAPManager config push failed: $msg — fill in manually at $DAP_URL or via Settings."
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "  ✓ DAPManager   $DAP_URL"
if [ "$SKIP_ARR" != "1" ]; then
    echo "  ✓ Lidarr       $LIDARR_URL"
    echo "  ✓ Prowlarr     $PROWLARR_URL  ← add indexers here"
fi
echo ""

if [ -n "$MASTER_PUBLIC_URL" ]; then
    DL_LINK="${MASTER_PUBLIC_URL%/}/download/mac"
    [ -n "$API_TOKEN" ] && DL_LINK="${DL_LINK}?token=${API_TOKEN}"
    echo "  Satellite download link:"
    echo "    $DL_LINK"
    echo ""
fi

echo "  Next steps:"
[ -z "$SLSK_USERNAME" ] && echo "    1. Add Soulseek credentials at $DAP_URL (Settings → Soulseek)"
echo "    • Add indexers in Prowlarr ($PROWLARR_URL) — they sync into Lidarr automatically"
echo "    • (Optional) Add Jellyfin credentials in Settings if not passed above"
echo ""
echo "  Full log: $LOG_FILE"
