#requires -Version 5.1
[CmdletBinding()]
param([switch]$CleanInstall)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$webRoot = Join-Path $appRoot 'web\angular'
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'npm is required to build the Angular application.' }
Push-Location $webRoot
try {
    if ($CleanInstall -and (Test-Path -LiteralPath 'package-lock.json')) { & npm ci }
    else { & npm install }
    if ($LASTEXITCODE -ne 0) { throw 'npm dependency installation failed.' }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Angular production build failed.' }
}
finally { Pop-Location }
Write-Host "Angular bundle created under '$appRoot\web\dist\artifex-chat'."
