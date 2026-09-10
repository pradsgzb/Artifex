#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$OutputPath = (Join-Path $PSScriptRoot 'release\Artifex-lite.zip')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$releaseRoot = Split-Path -Parent $resolvedOutput
$stage = Join-Path $env:TEMP ("Artifex-lite-" + [Guid]::NewGuid().ToString('N'))
$appStage = Join-Path $stage 'Artifex'

New-Item -ItemType Directory -Force -Path $appStage, $releaseRoot | Out-Null
try {
    foreach ($dir in @('bin','config','qwen_archive','web')) {
        Copy-Item -LiteralPath (Join-Path $root $dir) -Destination (Join-Path $appStage $dir) -Recurse -Force
    }
    foreach ($file in @(
        'qwen_archive.py', 'setup.ps1', 'verify.ps1', 'make-lite-package.ps1',
        'requirements.txt', 'requirements-server.txt', 'requirements-optional.txt', 'README.md', 'DEPLOYMENT.md',
        'ARCHITECTURE.md', 'API.md', 'CHANGELOG.md'
    )) {
        $source = Join-Path $root $file
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $appStage $file) -Force
        }
    }

    # settings.json is the only user settings file. Never carry a legacy app.json
    # from an upgraded development tree into a newly generated Lite package.
    Remove-Item -LiteralPath (Join-Path $appStage 'config\app.json') -Force -ErrorAction SilentlyContinue

    Get-ChildItem -LiteralPath $appStage -Directory -Recurse -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $appStage -File -Recurse -Include '*.pyc','*.pyo' -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $appStage 'web\angular\node_modules') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $appStage 'web\dist') -Recurse -Force -ErrorAction SilentlyContinue

    # These empty directories document the final portable layout before setup fills them.
    foreach ($dir in @('runtime','models','tools','cache','logs','deployment')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $appStage $dir) | Out-Null
    }

    Remove-Item -LiteralPath $resolvedOutput -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path $appStage -DestinationPath $resolvedOutput -CompressionLevel Optimal
    Write-Host "Lite bootstrap package created: $resolvedOutput" -ForegroundColor Green
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
