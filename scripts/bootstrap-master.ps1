<#
.SYNOPSIS
    Bring up the master container with MASTER_PUBLIC_URL set so satellites
    can be onboarded without typing the master's address.

.DESCRIPTION
    Detects the host's Tailscale HTTPS URL from an existing root Serve route
    that proxies to the loopback DAPManager port, exports MASTER_PUBLIC_URL
    into the process env, and runs 'docker compose up -d'.

    Override env vars:
      MASTER_PUBLIC_URL   skip detection, use this URL verbatim
      MASTER_PORT         local DAPManager port matched in Serve (default 5001)
      COMPOSE_FILE        compose file to launch          (default docker-compose.yml)

    POSIX hosts: use scripts/bootstrap-master.sh.

.PARAMETER PrintOnly
    Detect + print the URL, then skip 'docker compose up'.
#>

[CmdletBinding()]
param(
    [Alias('n')]
    [switch]$PrintOnly
)

$ErrorActionPreference = 'Stop'

function Write-Log { param([string]$Message) Write-Host "[bootstrap-master] $Message" }

$MasterPort  = if ($env:MASTER_PORT)  { $env:MASTER_PORT }  else { '5001' }
$ComposeFile = if ($env:COMPOSE_FILE) { $env:COMPOSE_FILE } else { 'docker-compose.yml' }

function Resolve-TailscaleCli {
    foreach ($name in 'tailscale', 'tailscale.exe') {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    $candidate = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path $candidate) { return $candidate }
    return $null
}

function Get-TailscaleUrl {
    $cli = Resolve-TailscaleCli
    if (-not $cli) {
        Write-Log "tailscale CLI not on PATH"
        return $null
    }
    try {
        $raw = & $cli serve status --json 2>$null
    } catch {
        Write-Log "'tailscale serve status --json' failed"
        return $null
    }
    if (-not $raw) {
        Write-Log "'tailscale serve status --json' returned nothing"
        return $null
    }
    try {
        $status = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-Log "could not parse tailscale JSON output"
        return $null
    }
    $targets = @(
        "http://127.0.0.1:${MasterPort}",
        "http://localhost:${MasterPort}"
    )
    if ($null -ne $status.Web) {
        foreach ($site in @($status.Web.PSObject.Properties | Sort-Object Name)) {
            $handlers = $site.Value.Handlers
            if ($null -eq $handlers) { continue }
            $root = $handlers.PSObject.Properties['/']
            if ($null -eq $root) { continue }
            $proxy = ([string]$root.Value.Proxy).TrimEnd('/')
            if ($targets -contains $proxy) { return "https://$($site.Name)" }
        }
    }
    Write-Log "no root Tailscale Serve route proxies to http://127.0.0.1:${MasterPort}"
    return $null
}

if (-not $env:MASTER_PUBLIC_URL) {
    $detected = Get-TailscaleUrl
    if ($detected) {
        $env:MASTER_PUBLIC_URL = $detected
        Write-Log "detected MASTER_PUBLIC_URL = $detected"
    }
}

if (-not $env:MASTER_PUBLIC_URL) {
    Write-Log "MASTER_PUBLIC_URL is unset and Tailscale detection failed."
    Write-Log "Configure Tailscale Serve for 127.0.0.1:$MasterPort or set the HTTPS URL manually, e.g.:"
    Write-Log "  `$env:MASTER_PUBLIC_URL='https://your-host.example.ts.net:PORT'; .\scripts\bootstrap-master.ps1"
    exit 1
}

$env:MASTER_PORT = $MasterPort

if ($PrintOnly) {
    Write-Log "print-only mode; skipping docker compose."
    Write-Log "MASTER_PUBLIC_URL=$($env:MASTER_PUBLIC_URL)"
    exit 0
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Log "docker not on PATH"
    exit 1
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Log "docker compose v2 not available"
    exit 1
}

if (-not (Test-Path $ComposeFile)) {
    Write-Log "$ComposeFile missing — run from the repo root or set `$env:COMPOSE_FILE"
    exit 1
}

Write-Log "starting docker compose with MASTER_PUBLIC_URL in env…"
& docker compose -f $ComposeFile up -d
if ($LASTEXITCODE -ne 0) {
    Write-Log "docker compose exited $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Log ""
Write-Log "master started. Download link for satellites:"
Write-Log "  $($env:MASTER_PUBLIC_URL)/download/mac"
