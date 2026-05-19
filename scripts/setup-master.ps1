<#
.SYNOPSIS
    One-command Windows master install: Docker containers up, Prowlarr wired
    to Lidarr, credentials pushed to DAPManager — no browser tabs required.

.DESCRIPTION
    Replaces the bash-only setup-master.sh + bootstrap-arr.sh combo with a
    native PowerShell script that works on any Windows host with Docker Desktop.

    What it does (in order):
      1. Detect the host's Tailscale URL (or accept one via -MasterPublicUrl).
      2. Copy docker-compose.example.yml → docker-compose.yml on first run.
      3. Start all containers: dapmanager + lidarr + prowlarr (--profile arr).
      4. Wait for every service to pass its health/ping endpoint.
      5. Read API keys from lidarr-config\config.xml and prowlarr-config\config.xml
         (pure PowerShell XML — no jq, no curl required).
      6. Wire Prowlarr → Lidarr (idempotent: skips if already registered).
      7. Set Lidarr root folder to /music (idempotent).
      8. Push to DAPManager via POST /api/config:
            lidarr_url, lidarr_api_key, lidarr_enabled
            public_master_url  (from Tailscale / -MasterPublicUrl)
            slsk_username, slsk_password  (params / env / interactive prompt)
            jellyfin_url, jellyfin_api_key, jellyfin_user_id  (params / env, optional)
      9. Print a summary and the satellite download link.

    Credential priority for each value:
      1. Explicit parameter   (-SlskUsername "foo")
      2. Environment variable ($env:SLSK_USERNAME)
      3. Interactive prompt   (only for Soulseek — Jellyfin is silently skipped
                               when not provided so the wizard path stays clean)

.PARAMETER MasterPublicUrl
    Override the Tailscale-detected URL, e.g. http://mybox:5001.
    Mandatory when Tailscale isn't installed or detection fails.

.PARAMETER MasterPort
    Port used when composing the Tailscale URL. Default 5001.

.PARAMETER SlskUsername
    Soulseek username. Falls back to $env:SLSK_USERNAME, then prompts.

.PARAMETER SlskPassword
    Soulseek password. Falls back to $env:SLSK_PASSWORD, then prompts.

.PARAMETER JellyfinUrl
    Jellyfin server URL. Falls back to $env:JELLYFIN_URL. Optional.

.PARAMETER JellyfinApiKey
    Jellyfin API key. Falls back to $env:JELLYFIN_API_KEY. Optional.

.PARAMETER JellyfinUserId
    Jellyfin user ID. Falls back to $env:JELLYFIN_USER_ID. Optional.

.PARAMETER DapManagerUrl
    URL the script uses to reach DAPManager. Default http://localhost:5001.

.PARAMETER LidarrUrl
    URL the script uses to reach Lidarr. Default http://localhost:8686.

.PARAMETER LidarrInternalUrl
    URL Prowlarr uses to reach Lidarr inside the compose network.
    Default http://lidarr:8686.

.PARAMETER ProwlarrUrl
    URL the script uses to reach Prowlarr. Default http://localhost:9696.

.PARAMETER ProwlarrInternalUrl
    URL Prowlarr uses to refer to itself in callbacks.
    Default http://prowlarr:9696.

.PARAMETER ComposeFile
    Path to the docker-compose file. Default .\docker-compose.yml.

.PARAMETER SkipArr
    Start DAPManager only; skip Lidarr + Prowlarr.

.PARAMETER PrintOnly
    Detect Tailscale URL and print it, then exit without starting anything.

.EXAMPLE
    .\scripts\setup-master.ps1

.EXAMPLE
    .\scripts\setup-master.ps1 -SlskUsername "myuser" -SlskPassword "mypass"

.EXAMPLE
    $env:SLSK_USERNAME="myuser"; $env:SLSK_PASSWORD="mypass"
    .\scripts\setup-master.ps1

.EXAMPLE
    .\scripts\setup-master.ps1 -MasterPublicUrl "http://100.99.1.2:5001" -SkipArr
#>

[CmdletBinding()]
param(
    [string]$MasterPublicUrl   = $env:MASTER_PUBLIC_URL,
    [string]$MasterPort        = ($env:MASTER_PORT -or '5001'),
    [string]$SlskUsername      = $env:SLSK_USERNAME,
    [string]$SlskPassword      = $env:SLSK_PASSWORD,
    [string]$JellyfinUrl       = $env:JELLYFIN_URL,
    [string]$JellyfinApiKey    = $env:JELLYFIN_API_KEY,
    [string]$JellyfinUserId    = $env:JELLYFIN_USER_ID,
    [string]$DapManagerUrl     = 'http://localhost:5001',
    [string]$LidarrUrl         = 'http://localhost:8686',
    [string]$LidarrInternalUrl = 'http://lidarr:8686',
    [string]$ProwlarrUrl       = 'http://localhost:9696',
    [string]$ProwlarrInternalUrl = 'http://prowlarr:9696',
    [string]$RutrackerUsername = $env:RUTRACKER_USERNAME,
    [string]$RutrackerPassword = $env:RUTRACKER_PASSWORD,
    [string]$ComposeFile       = '.\docker-compose.yml',
    [switch]$SkipArr,
    [switch]$PrintOnly
)

$ErrorActionPreference = 'Stop'

# ── Logging ──────────────────────────────────────────────────────────────────

function Log  { param([string]$m) Write-Host "[$(Get-Date -f 'HH:mm:ss')] $m" }
function Ok   { param([string]$m) Write-Host "[$(Get-Date -f 'HH:mm:ss')] [OK] $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "[$(Get-Date -f 'HH:mm:ss')] [WARN] $m" -ForegroundColor Yellow }
function Fail { param([string]$m) Write-Host "[$(Get-Date -f 'HH:mm:ss')] [ERROR] $m" -ForegroundColor Red; exit 1 }

Log "setup-master starting"

# ── Prerequisites ─────────────────────────────────────────────────────────────

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "docker not found on PATH. Install Docker Desktop." }
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail "docker compose v2 not available. Update Docker Desktop." }
Ok "docker OK"

# ── Tailscale detection ───────────────────────────────────────────────────────

function Get-TailscaleUrl {
    foreach ($name in 'tailscale', 'tailscale.exe') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $cli = $cmd.Source; break }
    }
    if (-not $cli) {
        $candidate = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
        if (Test-Path $candidate) { $cli = $candidate }
    }
    if (-not $cli) { Warn "tailscale CLI not found — set -MasterPublicUrl manually"; return $null }

    try { $raw = & $cli status --json 2>$null } catch { Warn "tailscale status failed"; return $null }
    if (-not $raw) { Warn "tailscale status returned nothing (daemon down?)"; return $null }
    try { $status = $raw | ConvertFrom-Json -ErrorAction Stop } catch { Warn "could not parse tailscale JSON"; return $null }

    $self = $status.Self
    if (-not $self) { Warn "tailscale Self block missing"; return $null }

    $dns = ($self.DNSName -replace '\.$', '')
    if ($dns) { return "http://${dns}:${MasterPort}" }
    foreach ($ip in @($self.TailscaleIPs)) {
        if ($ip -and $ip -notmatch ':') { return "http://${ip}:${MasterPort}" }
    }
    Warn "no usable Tailscale identity (MagicDNS off and no IPv4?)"
    return $null
}

if (-not $MasterPublicUrl) {
    $MasterPublicUrl = Get-TailscaleUrl
    if ($MasterPublicUrl) { Ok "detected MASTER_PUBLIC_URL = $MasterPublicUrl" }
    else { Warn "MASTER_PUBLIC_URL not set — satellite distribution will be unavailable until you set it in Settings." }
}

if ($PrintOnly) {
    if ($MasterPublicUrl) { Log "MASTER_PUBLIC_URL = $MasterPublicUrl" }
    else { Log "MASTER_PUBLIC_URL = (not detected)" }
    exit 0
}

# ── Compose file ──────────────────────────────────────────────────────────────

if (-not (Test-Path $ComposeFile)) {
    $example = '.\docker-compose.example.yml'
    if (-not (Test-Path $example)) { Fail "$example not found — run from the repo root." }
    Copy-Item $example $ComposeFile
    Ok "copied $example → $ComposeFile"
} else {
    Log "$ComposeFile already exists — leaving it alone"
}

# ── Start containers ──────────────────────────────────────────────────────────

$env:MASTER_PUBLIC_URL = $MasterPublicUrl
$env:MASTER_PORT       = $MasterPort

$profile = if ($SkipArr) { @() } else { @('--profile', 'arr') }
Log "starting containers (profile: $(if ($SkipArr) { 'default' } else { 'arr' }))…"
& docker compose -f $ComposeFile @profile up -d
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed (exit $LASTEXITCODE)" }
Ok "containers started"

# ── Wait helpers ──────────────────────────────────────────────────────────────

function Wait-Http {
    param([string]$Url, [string]$Name, [int]$TimeoutSec = 120)
    Log "waiting for $Name at $Url…"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($r.StatusCode -lt 400) { Ok "$Name is up"; return }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Fail "$Name did not respond within ${TimeoutSec}s ($Url)"
}

function Read-XmlApiKey {
    param([string]$ConfigDir, [string]$ServiceName, [int]$TimeoutSec = 120)
    $xmlPath = Join-Path $ConfigDir 'config.xml'
    Log "waiting for $ServiceName config.xml at $xmlPath…"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Path $xmlPath) {
            try {
                [xml]$cfg = Get-Content $xmlPath -Raw -ErrorAction Stop
                $key = $cfg.Config.ApiKey
                if ($key -and $key.Length -gt 4) { Ok "read $ServiceName API key"; return $key }
            } catch { }
        }
        Start-Sleep -Seconds 2
    }
    Fail "timed out waiting for $ServiceName API key in $xmlPath"
}

# ── DAPManager health ─────────────────────────────────────────────────────────

Wait-Http -Url "$DapManagerUrl/api/healthz" -Name "DAPManager"

# ── Arr stack ─────────────────────────────────────────────────────────────────

if (-not $SkipArr) {
    Wait-Http -Url "$LidarrUrl/api/v1/system/status"  -Name "Lidarr"
    Wait-Http -Url "$ProwlarrUrl/ping"                 -Name "Prowlarr"

    $lidarrKey  = Read-XmlApiKey -ConfigDir '.\lidarr-config'  -ServiceName 'Lidarr'
    $prowlarrKey = Read-XmlApiKey -ConfigDir '.\prowlarr-config' -ServiceName 'Prowlarr'

    # ── Prowlarr → Lidarr wiring ──────────────────────────────────────────────

    function Invoke-Arr {
        param([string]$Method, [string]$ApiKey, [string]$Url, [object]$Body)
        $headers = @{ 'X-Api-Key' = $ApiKey; 'Content-Type' = 'application/json' }
        $params  = @{ Method = $Method; Uri = $Url; Headers = $headers; UseBasicParsing = $true; ErrorAction = 'Stop' }
        if ($Body) { $params['Body'] = ($Body | ConvertTo-Json -Depth 10) }
        return Invoke-WebRequest @params
    }

    Log "checking existing Prowlarr application registrations…"
    try {
        $resp     = Invoke-Arr GET $prowlarrKey "$ProwlarrUrl/api/v1/applications"
        $existing = $resp.Content | ConvertFrom-Json
        $already  = $existing | Where-Object {
            $_.implementation -eq 'Lidarr' -and
            ($_.fields | Where-Object { $_.name -eq 'baseUrl' -and $_.value -eq $LidarrInternalUrl })
        }
    } catch { Fail "could not query Prowlarr applications: $_" }

    if ($already) {
        Ok "Lidarr already registered in Prowlarr — skipping"
    } else {
        Log "registering Lidarr with Prowlarr…"
        $payload = @{
            name               = 'Lidarr'
            syncLevel          = 'fullSync'
            implementation     = 'Lidarr'
            implementationName = 'Lidarr'
            configContract     = 'LidarrSettings'
            fields             = @(
                @{ name = 'prowlarrUrl';    value = $ProwlarrInternalUrl },
                @{ name = 'baseUrl';        value = $LidarrInternalUrl },
                @{ name = 'apiKey';         value = $lidarrKey },
                @{ name = 'syncCategories'; value = @(3000,3010,3020,3030,3040,3050,3060) }
            )
            tags = @()
        }
        try {
            Invoke-Arr POST $prowlarrKey "$ProwlarrUrl/api/v1/applications" -Body $payload | Out-Null
            Ok "Prowlarr → Lidarr registered"
        } catch { Fail "Prowlarr rejected application payload: $_" }
    }

    # ── Lidarr root folder ────────────────────────────────────────────────────

    Log "checking Lidarr root folders…"
    try {
        $rfResp    = Invoke-Arr GET $lidarrKey "$LidarrUrl/api/v1/rootFolder"
        $rootFolders = $rfResp.Content | ConvertFrom-Json
        $hasMusic  = $rootFolders | Where-Object { $_.path -eq '/music' }
    } catch { Fail "could not query Lidarr root folders: $_" }

    if ($hasMusic) {
        Ok "Lidarr root folder /music already set — skipping"
    } else {
        Log "setting Lidarr root folder to /music…"
        try {
            Invoke-Arr POST $lidarrKey "$LidarrUrl/api/v1/rootFolder" -Body @{ path = '/music' } | Out-Null
            Ok "Lidarr root folder /music set"
        } catch { Fail "could not set Lidarr root folder: $_" }
    }

    # ── qBittorrent ───────────────────────────────────────────────────────────

    $QbtUrl = 'http://localhost:8080'
    Wait-Http -Url "$QbtUrl" -Name "qBittorrent"

    # linuxserver/qbittorrent logs a generated temp password on first boot.
    # Parse it, set a stable generated password via the WebAPI, then wire
    # qBittorrent into Lidarr so downloads route automatically.
    function New-RandomPassword {
        $chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        -join (1..24 | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    }

    Log "reading qBittorrent temporary password from container logs…"
    $qbtTempPass = $null
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline -and -not $qbtTempPass) {
        $logs = & docker logs qbittorrent 2>&1
        $match = ($logs | Select-String 'temporary password[^:]*:\s*(\S+)' | Select-Object -Last 1)
        if ($match) {
            $qbtTempPass = $match.Matches[0].Groups[1].Value.Trim()
        }
        if (-not $qbtTempPass) { Start-Sleep -Seconds 3 }
    }

    if (-not $qbtTempPass) {
        Warn "could not read qBittorrent temp password from logs — skipping qBittorrent wiring."
        Warn "Visit $QbtUrl to configure manually, then add it in Lidarr Settings → Download Clients."
    } else {
        Ok "got qBittorrent temp password"

        # Login with temp password to get a session cookie
        try {
            $qbtSession = $null
            $loginResp = Invoke-WebRequest -Method POST -Uri "$QbtUrl/api/v2/auth/login" `
                -ContentType 'application/x-www-form-urlencoded' `
                -Body "username=admin&password=$qbtTempPass" `
                -SessionVariable qbtSession -UseBasicParsing -ErrorAction Stop
            if ($loginResp.Content -notmatch 'Ok') {
                Warn "qBittorrent login failed (wrong temp password?) — skipping qBittorrent wiring."
                $qbtSession = $null
            }
        } catch {
            Warn "qBittorrent login request failed: $_ — skipping qBittorrent wiring."
            $qbtSession = $null
        }

        if ($qbtSession) {
            # Set a stable generated password
            $qbtPassword = New-RandomPassword
            $prefsJson   = [uri]::EscapeDataString("{`"web_ui_password`":`"$qbtPassword`"}")
            try {
                Invoke-WebRequest -Method POST -Uri "$QbtUrl/api/v2/app/setPreferences" `
                    -ContentType 'application/x-www-form-urlencoded' `
                    -Body "json=$prefsJson" `
                    -WebSession $qbtSession -UseBasicParsing -ErrorAction Stop | Out-Null
                Ok "qBittorrent password set"
            } catch { Warn "could not set qBittorrent password: $_" }

            # Check whether Lidarr already has a qBittorrent download client
            $dcResp = Invoke-Arr GET $lidarrKey "$LidarrUrl/api/v1/downloadclient"
            $existingQbt = ($dcResp.Content | ConvertFrom-Json) |
                Where-Object { $_.implementation -eq 'QBittorrent' }

            if ($existingQbt) {
                Ok "qBittorrent already registered in Lidarr — skipping"
            } else {
                Log "adding qBittorrent to Lidarr…"
                $dcPayload = @{
                    name               = 'qBittorrent'
                    enable             = $true
                    protocol           = 'torrent'
                    implementation     = 'QBittorrent'
                    implementationName = 'qBittorrent'
                    configContract     = 'QBittorrentSettings'
                    fields             = @(
                        @{ name = 'host';          value = 'qbittorrent' }
                        @{ name = 'port';          value = 8080 }
                        @{ name = 'useSsl';        value = $false }
                        @{ name = 'username';      value = 'admin' }
                        @{ name = 'password';      value = $qbtPassword }
                        @{ name = 'musicCategory'; value = 'music' }
                        @{ name = 'initialState';  value = 0 }
                    )
                    tags = @()
                }
                try {
                    Invoke-Arr POST $lidarrKey "$LidarrUrl/api/v1/downloadclient" -Body $dcPayload | Out-Null
                    Ok "qBittorrent added to Lidarr"
                } catch { Warn "could not add qBittorrent to Lidarr: $_ — add manually in Lidarr Settings → Download Clients." }
            }
        }
    }

    # ── Public Prowlarr indexers (no credentials required) ────────────────────

    Log "fetching available Prowlarr indexer schemas…"
    try {
        $schemaResp = Invoke-Arr GET $prowlarrKey "$ProwlarrUrl/api/v1/indexer/schema"
        $allSchemas = $schemaResp.Content | ConvertFrom-Json
        $publicSchemas = $allSchemas | Where-Object { $_.privacy -eq 'Public' }
        Log "found $($publicSchemas.Count) public indexers in Prowlarr catalog"
    } catch {
        Warn "could not fetch Prowlarr indexer schemas: $_ — add indexers manually at $ProwlarrUrl"
        $publicSchemas = @()
    }

    if ($publicSchemas.Count -gt 0) {
        # Fetch already-added indexers so we can skip duplicates (idempotent)
        $existingIndexers = @()
        try {
            $eiResp = Invoke-Arr GET $prowlarrKey "$ProwlarrUrl/api/v1/indexer"
            $existingIndexers = ($eiResp.Content | ConvertFrom-Json) | ForEach-Object { $_.name }
        } catch { }

        $added = 0; $skipped = 0
        foreach ($schema in $publicSchemas) {
            if ($existingIndexers -contains $schema.name) { $skipped++; continue }
            $indexerPayload = @{
                name                    = $schema.name
                enableRss               = $true
                enableAutomaticSearch   = $true
                enableInteractiveSearch = $true
                protocol                = $schema.protocol
                implementation          = $schema.implementation
                implementationName      = $schema.implementationName
                configContract          = $schema.configContract
                fields                  = $schema.fields   # use schema defaults
                tags                    = @()
            }
            try {
                Invoke-Arr POST $prowlarrKey "$ProwlarrUrl/api/v1/indexer" -Body $indexerPayload | Out-Null
                $added++
            } catch {
                # Non-fatal — some public schemas require a baseUrl tweak; skip silently
            }
        }
        Ok "Prowlarr public indexers: added $added, skipped $skipped already-present"
        if ($added -gt 0) {
            Log "  → indexers auto-synced into Lidarr. Add private indexers at $ProwlarrUrl"
        }
    }

    # ── Private indexers with credentials ────────────────────────────────────
    # Pass RUTRACKER_USERNAME + RUTRACKER_PASSWORD (or -RutrackerUsername/-Password)
    # to auto-register Rutracker in Prowlarr.  Other private trackers can be added
    # the same way below — just clone the block and change the implementation name.

    function Add-PrivateIndexer {
        param(
            [string]$IndexerName,       # Prowlarr implementation name, e.g. 'Rutracker'
            [string]$DisplayName,       # Label shown in Prowlarr UI
            [string]$Username,
            [string]$Password,
            [hashtable]$ExtraFields = @{}
        )
        if (-not $Username -or -not $Password) { return }

        # Check if already present
        $existing = @()
        try {
            $eiR = Invoke-Arr GET $prowlarrKey "$ProwlarrUrl/api/v1/indexer"
            $existing = ($eiR.Content | ConvertFrom-Json) | ForEach-Object { $_.name }
        } catch { }
        if ($existing -contains $DisplayName) {
            Ok "$DisplayName already in Prowlarr — skipping"
            return
        }

        # Find schema
        $schema = $allSchemas | Where-Object { $_.implementation -eq $IndexerName } | Select-Object -First 1
        if (-not $schema) {
            Warn "$IndexerName schema not found in Prowlarr (old Prowlarr version?); skipping."
            return
        }

        # Merge username/password into the schema fields
        $fields = @($schema.fields) | ForEach-Object {
            $f = @{ name = $_.name; value = $_.value }
            if ($_.name -eq 'username') { $f['value'] = $Username }
            if ($_.name -eq 'password') { $f['value'] = $Password }
            foreach ($k in $ExtraFields.Keys) {
                if ($_.name -eq $k) { $f['value'] = $ExtraFields[$k] }
            }
            $f
        }

        $payload = @{
            name                    = $DisplayName
            enableRss               = $true
            enableAutomaticSearch   = $true
            enableInteractiveSearch = $true
            protocol                = $schema.protocol
            implementation          = $schema.implementation
            implementationName      = $schema.implementationName
            configContract          = $schema.configContract
            fields                  = $fields
            tags                    = @()
        }
        try {
            Invoke-Arr POST $prowlarrKey "$ProwlarrUrl/api/v1/indexer" -Body $payload | Out-Null
            Ok "$DisplayName added to Prowlarr"
        } catch {
            Warn "could not add $DisplayName to Prowlarr: $_ — add manually at $ProwlarrUrl"
        }
    }

    if ($allSchemas) {
        Add-PrivateIndexer -IndexerName 'Rutracker' -DisplayName 'Rutracker' `
            -Username $RutrackerUsername -Password $RutrackerPassword
    }
}

# ── Credentials ───────────────────────────────────────────────────────────────

# Soulseek — prompt if missing (required for downloads)
if (-not $SlskUsername) {
    $SlskUsername = Read-Host "Soulseek username (leave blank to skip)"
}
if ($SlskUsername -and -not $SlskPassword) {
    $securePass = Read-Host "Soulseek password" -AsSecureString
    $SlskPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePass)
    )
}

# ── Push config to DAPManager ─────────────────────────────────────────────────

Log "pushing configuration to DAPManager…"

$patch = @{}

if ($MasterPublicUrl) { $patch['public_master_url'] = $MasterPublicUrl.TrimEnd('/') }
if ($SlskUsername)    { $patch['slsk_username']      = $SlskUsername }
if ($SlskPassword)    { $patch['slsk_password']      = $SlskPassword }
if ($JellyfinUrl)     { $patch['jellyfin_url']       = $JellyfinUrl }
if ($JellyfinApiKey)  { $patch['jellyfin_api_key']   = $JellyfinApiKey }
if ($JellyfinUserId)  { $patch['jellyfin_user_id']   = $JellyfinUserId }

if (-not $SkipArr) {
    $patch['lidarr_enabled'] = $true
    $patch['lidarr_url']     = $LidarrInternalUrl   # container-to-container URL
    $patch['lidarr_api_key'] = $lidarrKey
}

# Read api_token from config.json if present so we can auth the PATCH
$configFile = '.\config\config.json'
$apiToken   = $null
if (Test-Path $configFile) {
    try {
        $cfgJson  = Get-Content $configFile -Raw | ConvertFrom-Json
        $apiToken = $cfgJson.api_token
    } catch { }
}

$headers = @{ 'Content-Type' = 'application/json' }
if ($apiToken) { $headers['Authorization'] = "Bearer $apiToken" }

try {
    $body = $patch | ConvertTo-Json -Depth 5
    $r = Invoke-WebRequest -Method POST -Uri "$DapManagerUrl/api/config" `
         -Headers $headers -Body $body -UseBasicParsing -ErrorAction Stop
    $result = $r.Content | ConvertFrom-Json
    if ($result.success) {
        Ok "DAPManager config updated (changed: $($result.changed -join ', '))"
    } else {
        Warn "DAPManager config POST returned success=false: $($result.message)"
    }
} catch {
    Warn "could not push config to DAPManager: $_ — fill in manually at $DapManagerUrl/setup or Settings."
}

# ── Summary ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ✓ DAPManager   $DapManagerUrl" -ForegroundColor Green
if (-not $SkipArr) {
    Write-Host "  ✓ Lidarr       $LidarrUrl" -ForegroundColor Green
    Write-Host "  ✓ Prowlarr     $ProwlarrUrl  ← add indexers here" -ForegroundColor Green
}
Write-Host ""

if ($MasterPublicUrl) {
    $dlLink = "$($MasterPublicUrl.TrimEnd('/'))/download/mac"
    if ($apiToken) { $dlLink += "?token=$apiToken" }
    Write-Host "  Satellite download link:" -ForegroundColor Cyan
    Write-Host "    $dlLink" -ForegroundColor Cyan
    Write-Host ""
}

Write-Host "  Next steps:" -ForegroundColor Yellow
if (-not $SlskUsername) {
    Write-Host "    1. Add Soulseek credentials in Settings → Soulseek" -ForegroundColor Yellow
}
if (-not $RutrackerUsername) {
    Write-Host "    • For lossless music: register at https://rutracker.org then re-run with" -ForegroundColor Yellow
    Write-Host "      -RutrackerUsername <u> -RutrackerPassword <p>  (or env RUTRACKER_USERNAME/PASSWORD)" -ForegroundColor Yellow
}
Write-Host "    • Add more indexers in Prowlarr ($ProwlarrUrl) — they sync into Lidarr automatically" -ForegroundColor Yellow
Write-Host "    • (Optional) Add Jellyfin credentials in Settings if not passed above" -ForegroundColor Yellow
Write-Host ""
Write-Host "  Full log: this terminal session" -ForegroundColor DarkGray
