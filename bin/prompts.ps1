#requires -Version 5.1
[CmdletBinding()]
param(
    [Alias('In','InDir')][string]$InputPath = (Get-Location).Path,
    [switch]$Recurse,
    [ValidateRange(0,100000000)][int]$MaxFiles = 0,
    [string]$Config = '',
    [string]$Model = 'Qwen/Qwen3.5-4B',
    [string]$ModelCacheDir = '',
    [switch]$LocalFilesOnly,
    [ValidateSet('auto','cuda','cpu')][string]$Device = 'auto',
    [ValidateSet('auto','bfloat16','float16','float32')][string]$Precision = 'auto',
    [ValidateSet('auto','sdpa','eager','flash_attention_2')][string]$Attention = 'sdpa',
    [ValidateSet('none','8bit','4bit')][string]$Quantization = 'none',
    [ValidateRange(0.0,100.0)][double]$LlmInt8Threshold = 6.0,
    [switch]$CompileModel,
    [ValidateRange(32,131072)][int]$MaxNewTokens = 1024,
    [ValidateRange(0,86400)][double]$MaxInferenceSeconds = 180,
    [ValidateRange(0,32768)][int]$MaxImageSide = 1024,
    [switch]$AdaptiveGeneration,
    [switch]$NoAdaptiveGeneration,
    [ValidateRange(32,131072)][int]$MaxNewTokensCeiling = 3072,
    [ValidateRange(1.01,8.0)][double]$TokenGrowthFactor = 1.6,
    [ValidateRange(64,32768)][int]$MinimumImageSide = 640,
    [ValidateRange(0.1,0.99)][double]$ImageReductionFactor = 0.75,
    [ValidateRange(0.0,5.0)][double]$Temperature = 0.2,
    [ValidateRange(0.01,1.0)][double]$TopP = 0.8,
    [ValidateRange(0,1000)][int]$TopK = 20,
    [switch]$Sampling,
    [int]$Seed = 42,
    [ValidateRange(1,1000)][int]$BatchSize = 1,
    [ValidateRange(0,3600000)][int]$Delay = 0,
    [ValidateRange(0,10)][int]$Retries = 2,
    [ValidateRange(0.0,3600.0)][double]$RetryDelaySeconds = 1.0,
    [AllowEmptyString()][string]$Hint = '',
    [AllowEmptyString()][string]$Prompt = '',
    [switch]$PreferCommentsPrompt,
    [switch]$NoPreferCommentsPrompt,
    [switch]$DefaultCaptureMetadata,
    [switch]$NoDefaultCaptureMetadata,
    [switch]$Force,
    [AllowEmptyString()][string]$OutputRoot = '',
    [ValidateRange(0,1000000)][int]$BucketThreshold = 10,
    [ValidateRange(1,1000000)][int]$BucketSize = 250,
    [ValidateRange(0.0,1.0)][double]$MinConfidence = 0.75,
    [switch]$AnalyzeMissing,
    [switch]$NoAnalyzeMissing,
    [AllowEmptyString()][string]$CategoriesFile = '',
    [switch]$InferenceCache,
    [switch]$NoInferenceCache,
    [AllowEmptyString()][string]$CacheFile = '',
    [AllowEmptyString()][string]$StateFile = '',
    [string]$ExifToolPath = 'exiftool',
    [ValidateRange(1,10)][int]$MetadataAttempts = 3,
    [ValidateRange(1,3600)][int]$MetadataTimeoutSeconds = 45,
    [switch]$Preview,
    [switch]$DryRun,
    [ValidateSet('DEBUG','INFO','WARNING','ERROR','CRITICAL')][string]$LogLevel = 'INFO',
    [AllowEmptyString()][string]$LogPath = '',
    [switch]$Color,
    [switch]$NoColor,
    [AllowEmptyString()][string]$PythonPath = '',
    [AllowEmptyString()][string]$CliPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$portablePaths = Get-ArtifexPortablePaths -Root $appRoot
if ([string]::IsNullOrWhiteSpace($PythonPath)) { $PythonPath = $portablePaths.Python }
if ([string]::IsNullOrWhiteSpace($CliPath)) { $CliPath = $portablePaths.Cli }

if (-not $DryRun -and -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Portable Python runtime not found at '$PythonPath'. Run .\setup.ps1 once for the Lite package, or provide -PythonPath."
}

$exitCode = Invoke-QwenArchiveTask -Task 'prompts' -BoundParameters $PSBoundParameters `
    -InputPath $InputPath -PythonPath $PythonPath -CliPath $CliPath -AppRoot $appRoot -DryRun:$DryRun
exit $exitCode
