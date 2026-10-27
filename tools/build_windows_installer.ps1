param(
    [string]$AppName = 'RetroFile',
    [ValidatePattern('^\d+\.\d+\.\d+(\.\d+)?$')]
    [string]$Version = '2.0.1',
    [string]$CompilerPath = 'ISCC.exe'
)

$ErrorActionPreference = 'Stop'

try {
    $compiler = Get-Command -Name $CompilerPath -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Source
    if (-not $compiler -and $CompilerPath -eq 'ISCC.exe') {
        $compiler = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if (-not $compiler) {
        throw 'Inno Setup 6 is required. Install it from https://jrsoftware.org/isdl.php or run: winget install --id JRSoftware.InnoSetup --exact. For a custom location, set ISCC when calling make.'
    }

    $projectRoot = Split-Path -Parent $PSScriptRoot
    $executable = Join-Path $projectRoot "dist\$AppName.exe"
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "Missing application executable: $executable. Run make build-windows first."
    }

    $definition = Join-Path $PSScriptRoot 'windows_installer.iss'
    & $compiler "/DAppName=$AppName" "/DAppVersion=$Version" "/DProjectRoot=$projectRoot" $definition
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compilation failed with exit code $LASTEXITCODE."
    }

    $installer = Join-Path $projectRoot "dist\$AppName-$Version-Setup.exe"
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "Inno Setup did not create the expected installer: $installer"
    }
    Write-Host "Built Windows installer: $installer"
} catch {
    Write-Error -Message $_ -ErrorAction Continue
    exit 1
}