<#
.SYNOPSIS
    Safely patch the Docker master's integration credentials from SSH stdin.

.DESCRIPTION
    Reads one versioned JSON envelope from standard input, validates an exact
    allowlist, merges it into config\config.json, and atomically replaces the
    live file while retaining a timestamped backup. Values are never accepted
    as command-line parameters or read from environment variables.

    Pass -Restart to restart the dapmanager Compose service and verify
    /api/healthz. If health does not recover, the previous configuration is
    restored and the service is restarted again.

    Envelope shape:
      {
        "version": 1,
        "set": { "slsk_username": "..." },
        "clear": []
      }

    A non-empty clear array also requires -AllowClear. The API token can be
    rotated to a non-empty value but cannot be cleared.

.EXAMPLE
    ssh -T viggys-pc powershell.exe -NoLogo -NoProfile -NonInteractive `
      -ExecutionPolicy Bypass `
      -File C:/Users/Vignesh/Desktop/DAPManger/scripts/set-master-config.ps1 `
      -Restart < ~/.config/dapmanager/master-config.json
#>

[CmdletBinding()]
param(
    [ValidateRange(5, 600)]
    [int]$HealthTimeoutSeconds = 120,
    [switch]$Restart,
    [switch]$AllowClear,
    [switch]$ValidateOnly
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$StringKeys = @(
    'slsk_username',
    'slsk_password',
    'jellyfin_url',
    'jellyfin_api_key',
    'jellyfin_user_id',
    'jellyfin_music_library_path',
    'acoustid_api_key',
    'contact_email',
    'api_token',
    'lidarr_url',
    'lidarr_api_key'
)
$BooleanKeys = @(
    'lidarr_enabled',
    'lidarr_acquisition_handoff_enabled',
    'auto_tag_downloads'
)
$SecretKeys = @(
    'slsk_password',
    'jellyfin_api_key',
    'api_token',
    'acoustid_api_key',
    'lidarr_api_key'
)
$ClearableKeys = @($StringKeys | Where-Object { $_ -cne 'api_token' })
$AllowedEnvelopeKeys = @('version', 'set', 'clear')
$MaximumEnvelopeBytes = 131072

function Write-SafeStatus {
    param([string]$Message)
    [Console]::Out.WriteLine($Message)
}

function Stop-Safely {
    param([string]$Message)
    [Console]::Error.WriteLine("ERROR: $Message")
    exit 1
}

function Test-JsonObject {
    param([object]$Value)
    return (
        $null -ne $Value -and
        $Value -isnot [System.Array] -and
        $Value -isnot [string] -and
        $Value -isnot [System.ValueType] -and
        $null -ne $Value.PSObject
    )
}

function Get-Sha256Hex {
    param([byte[]]$Bytes)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha256.ComputeHash($Bytes)
        return ([System.BitConverter]::ToString($hashBytes) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-FileSha256Hex {
    param([string]$Path)
    return Get-Sha256Hex -Bytes ([System.IO.File]::ReadAllBytes($Path))
}

function Read-BoundedStandardInput {
    param([int]$MaximumBytes)

    $stream = [Console]::OpenStandardInput()
    $memory = New-Object System.IO.MemoryStream
    $buffer = New-Object byte[] 4096
    try {
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($memory.Length + $read -gt $MaximumBytes) {
                Stop-Safely 'The JSON envelope exceeds the 128 KiB limit.'
            }
            $memory.Write($buffer, 0, $read)
        }
        return $memory.ToArray()
    }
    finally {
        $memory.Dispose()
    }
}

function Convert-Utf8BytesToText {
    param([byte[]]$Bytes)
    $offset = 0
    if (
        $Bytes.Length -ge 3 -and
        $Bytes[0] -eq 0xEF -and
        $Bytes[1] -eq 0xBB -and
        $Bytes[2] -eq 0xBF
    ) {
        $offset = 3
    }
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    return $strictUtf8.GetString($Bytes, $offset, $Bytes.Length - $offset)
}

function Set-RestrictedFileAcl {
    param([string]$Path)

    $currentSidValue = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $sidValues = @($currentSidValue, 'S-1-5-18', 'S-1-5-32-544')
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)

    foreach ($sidValue in $sidValues) {
        $sid = [System.Security.Principal.SecurityIdentifier]::new($sidValue)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.InheritanceFlags]::None,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    [System.IO.File]::SetAccessControl($Path, $acl)

    # Keep the equivalent icacls policy explicit for Windows operators and
    # verify that Windows accepted the protected ACL.
    $previousErrorPreference = $ErrorActionPreference
    $aclExitCode = 1
    try {
        $ErrorActionPreference = 'Continue'
        & icacls.exe $Path '/inheritance:r' '/grant:r' `
            "*${currentSidValue}:(F)" `
            '*S-1-5-18:(F)' `
            '*S-1-5-32-544:(F)' 1>$null 2>$null
        $aclExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($aclExitCode -ne 0) {
        throw [System.InvalidOperationException]::new('ACL update failed.')
    }
}

function Set-RestrictedDirectoryAcl {
    param([string]$Path)

    $currentSidValue = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $sidValues = @($currentSidValue, 'S-1-5-18', 'S-1-5-32-544')
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = (
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    )

    foreach ($sidValue in $sidValues) {
        $sid = [System.Security.Principal.SecurityIdentifier]::new($sidValue)
        $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        [void]$acl.AddAccessRule($rule)
    }
    [System.IO.Directory]::SetAccessControl($Path, $acl)

    $previousErrorPreference = $ErrorActionPreference
    $aclExitCode = 1
    try {
        $ErrorActionPreference = 'Continue'
        & icacls.exe $Path '/inheritance:r' '/grant:r' `
            "*${currentSidValue}:(OI)(CI)(F)" `
            '*S-1-5-18:(OI)(CI)(F)' `
            '*S-1-5-32-544:(OI)(CI)(F)' 1>$null 2>$null
        $aclExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($aclExitCode -ne 0) {
        throw [System.InvalidOperationException]::new('Directory ACL update failed.')
    }
}

function New-RestrictedEmptyFile {
    param([string]$Path)

    $stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::None
    )
    $stream.Dispose()
    try {
        Set-RestrictedFileAcl -Path $Path
    }
    catch {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Write-RestrictedUtf8TextFile {
    param(
        [string]$Path,
        [string]$Text
    )
    New-RestrictedEmptyFile -Path $Path
    $utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $utf8WithoutBom)
}

function Write-RestrictedBytesFile {
    param(
        [string]$Path,
        [byte[]]$Bytes
    )
    New-RestrictedEmptyFile -Path $Path
    [System.IO.File]::WriteAllBytes($Path, $Bytes)
}

function Test-HttpUrl {
    param([string]$Value)
    $parsed = $null
    if (-not [System.Uri]::TryCreate($Value, [System.UriKind]::Absolute, [ref]$parsed)) {
        return $false
    }
    if (-not [string]::IsNullOrEmpty($parsed.UserInfo)) {
        return $false
    }
    return $parsed.Scheme -ceq 'http' -or $parsed.Scheme -ceq 'https'
}

function Test-DapManagerHealth {
    param([int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest `
                -Uri 'http://127.0.0.1:5001/api/healthz' `
                -UseBasicParsing `
                -TimeoutSec 5 `
                -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                $health = $response.Content | ConvertFrom-Json -ErrorAction Stop
                if ($health.ok -eq $true -and $health.initialized -eq $true) {
                    return $true
                }
            }
        }
        catch {
            # Startup failures are expected while the container is restarting.
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Restart-DapManager {
    param(
        [string]$DockerPath,
        [string]$ComposePath
    )
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $DockerPath compose -f $ComposePath restart dapmanager 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
}

function Restore-PreviousConfig {
    param(
        [string]$LivePath,
        [string]$BackupPath,
        [string]$FailedDirectory,
        [object]$OriginalAcl
    )
    $directory = Split-Path -Parent $LivePath
    $rollbackTemp = Join-Path $directory ('.config.rollback.{0}.tmp' -f [Guid]::NewGuid().ToString('N'))
    $failedBackup = Join-Path $FailedDirectory (
        'config.failed.{0}.{1}.json.bak' -f (Get-Date -Format 'yyyyMMdd-HHmmssfff'), [Guid]::NewGuid().ToString('N')
    )
    try {
        Write-RestrictedBytesFile `
            -Path $rollbackTemp `
            -Bytes ([System.IO.File]::ReadAllBytes($BackupPath))
        [System.IO.File]::Replace($rollbackTemp, $LivePath, $failedBackup, $true)

        $liveAclRestored = $true
        try {
            Set-Acl -LiteralPath $LivePath -AclObject $OriginalAcl
        }
        catch {
            $liveAclRestored = $false
        }

        $backupsSecured = $true
        try {
            Set-RestrictedFileAcl -Path $BackupPath
            Set-RestrictedFileAcl -Path $failedBackup
        }
        catch {
            $backupsSecured = $false
        }

        return [PSCustomObject]@{
            FailedBackupPath = $failedBackup
            LiveAclRestored = $liveAclRestored
            BackupsSecured = $backupsSecured
        }
    }
    finally {
        if (Test-Path -LiteralPath $rollbackTemp -PathType Leaf) {
            Remove-Item -LiteralPath $rollbackTemp -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($Restart -and $ValidateOnly) {
    Stop-Safely '-Restart cannot be combined with -ValidateOnly.'
}

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$ConfigPath = Join-Path $repoRoot 'config\config.json'
$ComposeFile = Join-Path $repoRoot 'docker-compose.yml'

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    Stop-Safely 'The target configuration file does not exist.'
}

try {
    $envelopeBytes = Read-BoundedStandardInput -MaximumBytes $MaximumEnvelopeBytes
    if ($envelopeBytes.Length -eq 0) {
        Stop-Safely 'Expected one JSON envelope on standard input.'
    }
    $rawEnvelope = Convert-Utf8BytesToText -Bytes $envelopeBytes
    if ([string]::IsNullOrWhiteSpace($rawEnvelope)) {
        Stop-Safely 'Expected one JSON envelope on standard input.'
    }
    $envelope = $rawEnvelope | ConvertFrom-Json -ErrorAction Stop
}
catch {
    Stop-Safely 'Standard input is not valid UTF-8 JSON.'
}
finally {
    $rawEnvelope = $null
    $envelopeBytes = $null
}

if (-not (Test-JsonObject -Value $envelope)) {
    Stop-Safely 'The JSON envelope must be an object.'
}

$envelopeNames = @($envelope.PSObject.Properties | ForEach-Object { $_.Name })
foreach ($requiredName in $AllowedEnvelopeKeys) {
    if ($envelopeNames -cnotcontains $requiredName) {
        Stop-Safely "The envelope is missing required field '$requiredName'."
    }
}
foreach ($name in $envelopeNames) {
    if ($AllowedEnvelopeKeys -cnotcontains $name) {
        Stop-Safely "The envelope contains unsupported field '$name'."
    }
}
if ($envelope.version -isnot [int] -or $envelope.version -ne 1) {
    Stop-Safely 'Only envelope version 1 is supported.'
}
if (-not (Test-JsonObject -Value $envelope.set)) {
    Stop-Safely "The envelope field 'set' must be an object."
}
if ($envelope.clear -isnot [System.Array]) {
    Stop-Safely "The envelope field 'clear' must be an array."
}

$setNames = @($envelope.set.PSObject.Properties | ForEach-Object { $_.Name })
$clearNames = @($envelope.clear)
$allowedSetKeys = @($StringKeys + $BooleanKeys)

foreach ($name in $setNames) {
    if ($allowedSetKeys -cnotcontains $name) {
        Stop-Safely "The set object contains unsupported key '$name'."
    }
}

$seenClearNames = @()
foreach ($name in $clearNames) {
    if ($name -isnot [string] -or [string]::IsNullOrWhiteSpace($name)) {
        Stop-Safely 'Every clear entry must be a non-empty string.'
    }
    if ($ClearableKeys -cnotcontains $name) {
        Stop-Safely "The clear array contains unsupported key '$name'."
    }
    if ($seenClearNames -ccontains $name) {
        Stop-Safely "The clear array contains duplicate key '$name'."
    }
    if ($setNames -ccontains $name) {
        Stop-Safely "A key cannot appear in both set and clear: '$name'."
    }
    $seenClearNames += $name
}
if ($clearNames.Count -gt 0 -and -not $AllowClear) {
    Stop-Safely 'A non-empty clear array requires -AllowClear.'
}

$validatedSet = @{}
foreach ($name in $setNames) {
    $value = $envelope.set.PSObject.Properties[$name].Value
    if ($StringKeys -ccontains $name) {
        if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value)) {
            Stop-Safely "Set value for '$name' must be a non-empty string; use clear when supported."
        }
        if ($SecretKeys -ccontains $name) {
            $validatedSet[$name] = $value
        }
        else {
            $validatedSet[$name] = $value.Trim()
        }
        if ($name -ceq 'api_token' -and $validatedSet[$name].Length -lt 32) {
            Stop-Safely "Set value for 'api_token' must contain at least 32 characters."
        }
        if (($name -ceq 'jellyfin_url' -or $name -ceq 'lidarr_url') -and
            -not (Test-HttpUrl -Value $validatedSet[$name])) {
            Stop-Safely "Set value for '$name' must be an absolute HTTP or HTTPS URL."
        }
        if ($name -ceq 'jellyfin_url' -or $name -ceq 'lidarr_url') {
            $validatedSet[$name] = $validatedSet[$name].TrimEnd('/')
        }
    }
    elseif ($BooleanKeys -ccontains $name) {
        if ($value -isnot [bool]) {
            Stop-Safely "Set value for '$name' must be a JSON Boolean."
        }
        $validatedSet[$name] = $value
    }
}

try {
    $originalBytes = [System.IO.File]::ReadAllBytes($ConfigPath)
    $originalHash = Get-Sha256Hex -Bytes $originalBytes
    $originalAcl = Get-Acl -LiteralPath $ConfigPath
    $originalText = Convert-Utf8BytesToText -Bytes $originalBytes
    $config = $originalText | ConvertFrom-Json -ErrorAction Stop
}
catch {
    Stop-Safely 'The current configuration is not readable UTF-8 JSON.'
}
finally {
    $originalText = $null
}

if (-not (Test-JsonObject -Value $config)) {
    Stop-Safely 'The current configuration must contain a JSON object.'
}
$roleProperty = $config.PSObject.Properties['device_role']
if ($null -eq $roleProperty -or $roleProperty.Value -cne 'master') {
    Stop-Safely 'This helper only updates a configuration whose device_role is master.'
}

$changedNames = @()
foreach ($name in $setNames) {
    $desiredValue = $validatedSet[$name]
    $property = $config.PSObject.Properties[$name]
    if ($null -eq $property) {
        $config | Add-Member -NotePropertyName $name -NotePropertyValue $desiredValue
        $changedNames += $name
    }
    elseif (-not [object]::Equals($property.Value, $desiredValue)) {
        $property.Value = $desiredValue
        $changedNames += $name
    }
}
foreach ($name in $clearNames) {
    $property = $config.PSObject.Properties[$name]
    if ($null -eq $property) {
        $config | Add-Member -NotePropertyName $name -NotePropertyValue ''
        $changedNames += $name
    }
    elseif ($property.Value -cne '') {
        $property.Value = ''
        $changedNames += $name
    }
}
$changedNames = @($changedNames | Sort-Object -Unique)
$requestedNames = @($setNames + $clearNames | Sort-Object -Unique)

if ($ValidateOnly) {
    Write-SafeStatus ("Validation passed. Requested keys: {0}" -f (
        $(if ($requestedNames.Count -gt 0) { $requestedNames -join ', ' } else { '(none)' })
    ))
    exit 0
}

if ($changedNames.Count -eq 0) {
    Write-SafeStatus 'No configuration changes were required; restart skipped.'
    exit 0
}

$dockerPath = $null
if ($Restart) {
    if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
        Stop-Safely 'The Compose file does not exist; configuration was not changed.'
    }
    $dockerCommand = Get-Command docker.exe -ErrorAction SilentlyContinue
    if ($null -eq $dockerCommand) {
        $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    }
    if ($null -eq $dockerCommand) {
        Stop-Safely 'Docker was not found; configuration was not changed.'
    }
    $dockerPath = $dockerCommand.Source
    $previousErrorPreference = $ErrorActionPreference
    $composeAvailable = $false
    try {
        $ErrorActionPreference = 'Continue'
        & $dockerPath compose version 1>$null 2>$null
        $composeAvailable = $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if (-not $composeAvailable) {
        Stop-Safely 'Docker Compose is unavailable; configuration was not changed.'
    }
}

$configDirectory = Split-Path -Parent $ConfigPath
$backupDirectory = Join-Path $configDirectory '.backups'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmssfff'
$unique = [Guid]::NewGuid().ToString('N')
$tempPath = Join-Path $configDirectory ('.config.{0}.tmp' -f $unique)
$backupPath = Join-Path $backupDirectory ('config.{0}.{1}.json.bak' -f $stamp, $unique)
$capturePath = Join-Path $backupDirectory ('.capture.{0}.json.bak' -f $unique)

try {
    if (-not (Test-Path -LiteralPath $backupDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $backupDirectory | Out-Null
    }
    Set-RestrictedDirectoryAcl -Path $backupDirectory

    $newJson = $config | ConvertTo-Json -Depth 50
    Write-RestrictedUtf8TextFile `
        -Path $tempPath `
        -Text ($newJson + [Environment]::NewLine)
    $newJson = $null
    Write-RestrictedBytesFile -Path $backupPath -Bytes $originalBytes

    if ((Get-FileSha256Hex -Path $ConfigPath) -cne $originalHash) {
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
        Stop-Safely 'The configuration changed concurrently; no update was applied.'
    }
}
catch {
    if (Test-Path -LiteralPath $tempPath -PathType Leaf) {
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
        Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
    }
    Stop-Safely 'The atomic configuration update failed; the prior file remains authoritative.'
}

try {
    [System.IO.File]::Replace($tempPath, $ConfigPath, $capturePath, $true)
}
catch {
    if (Test-Path -LiteralPath $tempPath -PathType Leaf) {
        Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
        Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
    }
    Stop-Safely 'The atomic configuration replacement failed; the prior file remains authoritative.'
}

try {
    Set-RestrictedFileAcl -Path $capturePath
    if ((Get-FileSha256Hex -Path $capturePath) -cne $originalHash) {
        $concurrentRestore = Restore-PreviousConfig `
            -LivePath $ConfigPath `
            -BackupPath $capturePath `
            -FailedDirectory $backupDirectory `
            -OriginalAcl $originalAcl
        Stop-Safely 'A concurrent update was detected and restored; this update was not applied.'
    }
    Set-RestrictedFileAcl -Path $ConfigPath
    Set-RestrictedFileAcl -Path $backupPath
}
catch {
    try {
        $restoreSource = if (Test-Path -LiteralPath $capturePath -PathType Leaf) {
            $capturePath
        }
        else {
            $backupPath
        }
        $postCommitRestore = Restore-PreviousConfig `
            -LivePath $ConfigPath `
            -BackupPath $restoreSource `
            -FailedDirectory $backupDirectory `
            -OriginalAcl $originalAcl
        Stop-Safely 'A post-commit security step failed; the previous configuration was restored.'
    }
    catch {
        Stop-Safely ("The update committed but automatic rollback failed. Known-good backup: {0}" -f $backupPath)
    }
}
finally {
    if (Test-Path -LiteralPath $capturePath -PathType Leaf) {
        Remove-Item -LiteralPath $capturePath -Force -ErrorAction SilentlyContinue
    }
}

Write-SafeStatus ("Changed keys: {0}" -f ($changedNames -join ', '))
Write-SafeStatus ("Backup: {0}" -f $backupPath)

if (-not $Restart) {
    Write-SafeStatus 'Configuration updated. DAPManager was not restarted.'
    exit 0
}

Write-SafeStatus 'Restarting the dapmanager service and checking health.'
$restartSucceeded = Restart-DapManager -DockerPath $dockerPath -ComposePath $ComposeFile
if ($restartSucceeded -and (Test-DapManagerHealth -TimeoutSeconds $HealthTimeoutSeconds)) {
    Write-SafeStatus 'DAPManager is healthy with the updated configuration.'
    exit 0
}

[Console]::Error.WriteLine('Updated configuration did not become healthy; starting automatic rollback.')
try {
    $rollbackResult = Restore-PreviousConfig `
        -LivePath $ConfigPath `
        -BackupPath $backupPath `
        -FailedDirectory $backupDirectory `
        -OriginalAcl $originalAcl
}
catch {
    Stop-Safely ("Automatic rollback failed. The previous configuration is retained at: {0}" -f $backupPath)
}

$rollbackRestarted = Restart-DapManager -DockerPath $dockerPath -ComposePath $ComposeFile
if ($rollbackRestarted -and (Test-DapManagerHealth -TimeoutSeconds $HealthTimeoutSeconds)) {
    [Console]::Error.WriteLine(
        "Rollback restored the previous configuration. Failed candidate: $($rollbackResult.FailedBackupPath)"
    )
    if (-not $rollbackResult.LiveAclRestored -or -not $rollbackResult.BackupsSecured) {
        [Console]::Error.WriteLine('Rollback succeeded, but an ACL requires manual inspection.')
    }
    exit 1
}

Stop-Safely 'The previous configuration was restored, but DAPManager is still unhealthy.'
