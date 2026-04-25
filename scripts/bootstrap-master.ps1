<#
.SYNOPSIS
    Bring up the master container with MASTER_PUBLIC_URL set so satellites
    can be onboarded without typing the master's address.

.DESCRIPTION
    Detects the host's Tailscale URL via 'tailscale status --json'
    (Self.DNSName, then the IPv4 fallback), composes
    http://<host>:<port>, exports MASTER_PUBLIC_URL into the process
    env, and runs 'docker compose up -d' from the current directory.

    Override env vars:
      MASTER_PUBLIC_URL   skip detection, use this URL verbatim
      MASTER_PORT         listen port baked into the URL  (default 5001)
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
        $raw = & $cli status --json 2>$null
    } catch {
        Write-Log "'tailscale status --json' failed (daemon down?)"
        return $null
    }
    if (-not $raw) {
        Write-Log "'tailscale status --json' returned nothing (daemon down?)"
        return $null
    }
    try {
        $status = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        Write-Log "could not parse tailscale JSON output"
        return $null
    }
    $self = $status.Self
    if (-not $self) {
        Write-Log "tailscale status missing Self block"
        return $null
    }
    $dns = $self.DNSName
    if ($dns) { $dns = $dns.TrimEnd('.') }
    if ($dns) { return "http://$dns`:$MasterPort" }
    foreach ($ip in @($self.TailscaleIPs)) {
        if ($ip -and $ip -notmatch ':') {
            return "http://$ip`:$MasterPort"
        }
    }
    Write-Log "no usable Tailscale identity (MagicDNS off + no IPv4?)"
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
    Write-Log "Set it manually and re-run, e.g.:"
    Write-Log "  `$env:MASTER_PUBLIC_URL='http://your-host:$MasterPort'; .\scripts\bootstrap-master.ps1"
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
