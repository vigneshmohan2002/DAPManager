# start-master.ps1
# Starts DAPManager master on this Windows machine.
#
# What it does:
#   1. Starts the dapmanager container (docker compose up -d)
#   2. Starts the lidarr container separately (--profile arr, lidarr service only)
#      NOTE: Prowlarr is intentionally skipped — it runs as a native Windows
#            process on this machine. Starting it via Docker would conflict on :9696.
#   3. Polls /api/healthz until dapmanager is ready (up to 2 min)
#   4. Prints the web UI URL

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Write-Log { param([string]$Msg) Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $Msg" }

# ── 1. Sanity checks ─────────────────────────────────────────────────────────
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Log "ERROR: docker not found on PATH. Is Docker Desktop running?"
    exit 1
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: docker compose v2 not available."
    exit 1
}
if (-not (Test-Path "docker-compose.yml")) {
    Write-Log "ERROR: docker-compose.yml not found. Run from the DAPManger folder."
    exit 1
}

# ── 2. Start dapmanager ───────────────────────────────────────────────────────
# Remove any stale container with the same name that wasn't created by Compose
$existing = & docker ps -a --filter "name=^/dapmanager$" --format "{{.ID}}" 2>$null
if ($existing) {
    Write-Log "Removing existing dapmanager container ($existing)..."
    & docker rm -f dapmanager | Out-Null
}

Write-Log "Starting dapmanager container..."
& docker compose up -d dapmanager
if ($LASTEXITCODE -ne 0) { Write-Log "ERROR: docker compose up failed."; exit 1 }

# ── 3. Start Lidarr (arr profile, explicit service - skips Prowlarr) ──────────
$existingLidarr = & docker ps -a --filter "name=^/lidarr$" --format "{{.ID}}" 2>$null
if ($existingLidarr) {
    Write-Log "Removing existing lidarr container ($existingLidarr)..."
    & docker rm -f lidarr | Out-Null
}
Write-Log "Starting lidarr container..."
& docker compose --profile arr up -d lidarr
if ($LASTEXITCODE -ne 0) { Write-Log "WARNING: lidarr failed to start (non-fatal)." }

# ── 4. Wait for dapmanager health ────────────────────────────────────────────
$url     = "http://localhost:5001"
$health  = "$url/api/healthz"
$tries   = 60   # 60 x 2s = 2 min
$ready   = $false

Write-Log "Waiting for dapmanager to be healthy at $health ..."
for ($i = 0; $i -lt $tries; $i++) {
    try {
        $resp = Invoke-RestMethod -Uri $health -TimeoutSec 3 -ErrorAction Stop
        if ($resp.status -eq "ok") { $ready = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}

if ($ready) {
    Write-Log "dapmanager is healthy."
} else {
    Write-Log "WARNING: dapmanager did not respond within 2 minutes. Check 'docker logs dapmanager'."
}

# ── 5. Summary ───────────────────────────────────────────────────────────────
Write-Log ""
Write-Log "===== DAPManager Master ====="
Write-Log "  Web UI   : $url"
Write-Log "  Setup    : $url/setup"
Write-Log "  Lidarr   : http://localhost:8686"
Write-Log "  Prowlarr : http://localhost:9696  (native Windows process - not managed here)"
Write-Log ""
Write-Log "First-run checklist (complete via $url/setup or POST /api/config):"
Write-Log "  1. Soulseek credentials  (slsk_username / slsk_password)"
Write-Log "  2. AcoustID API key      (free at https://acoustid.org/login)"
Write-Log "  3. Contact email         (used in MusicBrainz user-agent)"
Write-Log "  4. Jellyfin URL + API key + user ID"
Write-Log "  5. Lidarr API key        (copy from http://localhost:8686 Settings > General)"
Write-Log "     Set lidarr_url = http://host.docker.internal:8686"
Write-Log "  6. API token             (locks down /api/* endpoints)"
Write-Log "  7. DAP mount point       (e.g. E:\)"
