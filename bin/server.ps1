#requires -Version 5.1
[CmdletBinding()]
param(
    [AllowEmptyString()][string]$Config = '',
    [string]$HostAddress = '127.0.0.1',
    [ValidateRange(1,65535)][int]$Port = 8000,
    [switch]$PreloadModel,
    [switch]$NoPreloadModel,
    [string]$ApiKeyEnvironmentVariable = 'ARTIFEX_API_KEY',
    [switch]$AllowUnauthenticatedRemote,
    [string[]]$CorsOrigin = @(),
    [switch]$OpenBrowser,
    [switch]$NoOpenBrowser,
    [AllowEmptyString()][string]$Model = '',
    [AllowEmptyString()][string]$ModelCacheDir = '',
    [ValidateSet('auto','cuda','cpu')][string]$Device = 'auto',
    [ValidateSet('auto','bfloat16','float16','float32')][string]$Precision = 'auto',
    [ValidateSet('auto','sdpa','eager','flash_attention_2')][string]$Attention = 'sdpa',
    [ValidateSet('none','8bit','4bit')][string]$Quantization = 'none',
    [ValidateRange(32,131072)][int]$MaxNewTokens = 1024,
    [ValidateRange(0,32768)][int]$MaxImageSide = 1024,
    [ValidateRange(0,86400)][double]$MaxInferenceSeconds = 180,
    [ValidateSet('DEBUG','INFO','WARNING','ERROR','CRITICAL')][string]$LogLevel = 'INFO',
    [AllowEmptyString()][string]$LogPath = '',
    [switch]$Color,
    [switch]$NoColor,
    [AllowEmptyString()][string]$PythonPath = '',
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

if ($PreloadModel -and $NoPreloadModel) { throw 'Use either -PreloadModel or -NoPreloadModel, not both.' }
if ($OpenBrowser -and $NoOpenBrowser) { throw 'Use either -OpenBrowser or -NoOpenBrowser, not both.' }
if ($Color -and $NoColor) { throw 'Use either -Color or -NoColor, not both.' }

$appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$paths = Get-ArtifexPortablePaths -Root $appRoot
if ([string]::IsNullOrWhiteSpace($PythonPath)) { $PythonPath = $paths.Python }
if (-not $DryRun -and -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Portable Python runtime not found at '$PythonPath'. Run .\setup.ps1 once or provide -PythonPath."
}

$args = [System.Collections.Generic.List[string]]::new()
$args.Add('-m'); $args.Add('qwen_archive.server_cli')
if ($PSBoundParameters.ContainsKey('Config') -and -not [string]::IsNullOrWhiteSpace($Config)) { $args.Add('--config'); $args.Add($Config) }
if ($PSBoundParameters.ContainsKey('HostAddress')) { $args.Add('--host'); $args.Add($HostAddress) }
if ($PSBoundParameters.ContainsKey('Port')) { $args.Add('--port'); $args.Add([string]$Port) }
if ($PreloadModel) { $args.Add('--preload-model') }
if ($NoPreloadModel) { $args.Add('--no-preload-model') }
if ($PSBoundParameters.ContainsKey('ApiKeyEnvironmentVariable')) { $args.Add('--api-key-env'); $args.Add($ApiKeyEnvironmentVariable) }
if ($AllowUnauthenticatedRemote) { $args.Add('--allow-unauthenticated-remote') }
foreach ($origin in $CorsOrigin) { $args.Add('--cors-origin'); $args.Add($origin) }
if ($OpenBrowser) { $args.Add('--open-browser') }
if ($NoOpenBrowser) { $args.Add('--no-open-browser') }
if ($PSBoundParameters.ContainsKey('Model') -and -not [string]::IsNullOrWhiteSpace($Model)) { $args.Add('--model'); $args.Add($Model) }
if ($PSBoundParameters.ContainsKey('ModelCacheDir')) { $args.Add('--model-cache-dir'); $args.Add($ModelCacheDir) }
foreach ($item in @(
    @('Device','--device',$Device), @('Precision','--precision',$Precision), @('Attention','--attention',$Attention),
    @('Quantization','--quantization',$Quantization), @('MaxNewTokens','--max-new-tokens',$MaxNewTokens),
    @('MaxImageSide','--max-image-side',$MaxImageSide), @('MaxInferenceSeconds','--max-inference-seconds',$MaxInferenceSeconds),
    @('LogLevel','--log-level',$LogLevel), @('LogPath','--log-path',$LogPath)
)) {
    if ($PSBoundParameters.ContainsKey([string]$item[0])) { $args.Add([string]$item[1]); $args.Add([string]$item[2]) }
}
if ($Color) { $args.Add('--color') }
if ($NoColor) { $args.Add('--no-color') }

$command = Format-QwenCommand -PythonPath $PythonPath -Arguments $args.ToArray()
if ($DryRun) { Write-Host $command; exit 0 }

New-Item -ItemType Directory -Force -Path $paths.HuggingFaceCache, $paths.TorchCache | Out-Null
$env:HF_HOME = $paths.HuggingFaceCache
$env:HF_HUB_CACHE = Join-Path $paths.HuggingFaceCache 'hub'
$env:TORCH_HOME = $paths.TorchCache
$env:XDG_CACHE_HOME = $paths.CacheRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONNOUSERSITE = '1'

Push-Location $appRoot
try {
    & $PythonPath @($args.ToArray())
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
