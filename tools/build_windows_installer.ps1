param(
    [string]$AppName = 'RetroFile',
    [string]$Version = '',
    [string]$Python = 'python',
    [string]$VpkPath = 'vpk'
)

$ErrorActionPreference = 'Stop'

try {
    $releaseArguments = @('--app-name', $AppName, '--vpk', $VpkPath)
    if ($Version) {
        $releaseArguments += @('--version', $Version)
    }
    & $Python (Join-Path $PSScriptRoot 'build_release.py') @releaseArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Velopack packaging failed with exit code $LASTEXITCODE."
    }
} catch {
    Write-Error -Message $_ -ErrorAction Continue
    exit 1
}