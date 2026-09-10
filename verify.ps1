#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$RequireCuda
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$settingsPath = Join-Path $root 'config\settings.json'
if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
    throw "Artifex settings not found: $settingsPath"
}
$settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Resolve-ArtifexConfiguredPath {
    param([Parameter(Mandatory=$true)][string]$Value)
    $expanded = $Value.Replace('${APP_ROOT}', $root)
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $root $expanded))
}

$python = Resolve-ArtifexConfiguredPath ([string]$settings.paths.runtimePython)
$model = Resolve-ArtifexConfiguredPath ([string]$settings.paths.modelDirectory)
$exif = Resolve-ArtifexConfiguredPath ([string]$settings.paths.exifToolExecutable)
$cacheRoot = Resolve-ArtifexConfiguredPath ([string]$settings.paths.cacheRoot)
$hfCache = Resolve-ArtifexConfiguredPath ([string]$settings.paths.modelCacheDirectory)
$torchCache = Resolve-ArtifexConfiguredPath ([string]$settings.paths.torchCacheDirectory)

foreach ($item in @($python, $exif, (Join-Path $model 'config.json'), (Join-Path $model 'tokenizer_config.json'))) {
    if (-not (Test-Path -LiteralPath $item -PathType Leaf)) {
        throw "Portable deployment is incomplete. Missing: $item. Run .\setup.ps1 for a Lite package."
    }
}
if (-not ((Test-Path -LiteralPath (Join-Path $model 'model.safetensors') -PathType Leaf) -or
          (Test-Path -LiteralPath (Join-Path $model 'model.safetensors.index.json') -PathType Leaf))) {
    throw "Portable deployment is incomplete. Qwen model weights are missing under: $model"
}

$env:HF_HOME = $hfCache
$env:HF_HUB_CACHE = Join-Path $hfCache 'hub'
$env:TORCH_HOME = $torchCache
$env:XDG_CACHE_HOME = $cacheRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONNOUSERSITE = '1'

& $exif -ver | ForEach-Object { Write-Host "ExifTool=$($_)" }
if ($LASTEXITCODE -ne 0) { throw "ExifTool verification failed with exit code $LASTEXITCODE." }

$code = @'
import sys
from pathlib import Path
import torch
import transformers
import PIL
import safetensors
import huggingface_hub
from transformers import AutoConfig, AutoProcessor
import qwen_archive.cli

model = Path(sys.argv[1]).resolve()
AutoConfig.from_pretrained(str(model), local_files_only=True)
AutoProcessor.from_pretrained(str(model), local_files_only=True)
print("Python=" + sys.version.split()[0])
print("Transformers=" + transformers.__version__)
print("Torch=" + torch.__version__)
print("CUDA=" + str(torch.cuda.is_available()))
print("CUDA Runtime=" + str(torch.version.cuda))
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print("GPU=" + p.name)
    print("VRAM GB=" + str(round(p.total_memory / (1024 ** 3), 2)))
try:
    import bitsandbytes as bnb
    print("bitsandbytes=" + bnb.__version__)
except Exception as exc:
    print("bitsandbytes=unavailable: " + str(exc))
print("Application package import=OK")
print("Offline model metadata=OK")
'@
& $python -c $code $model
if ($LASTEXITCODE -ne 0) { throw "Portable Python/runtime verification failed with exit code $LASTEXITCODE." }

if ($RequireCuda) {
    & $python -c "import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)"
    if ($LASTEXITCODE -ne 0) { throw 'CUDA is required but unavailable. Install/update the NVIDIA display driver.' }
}

Write-Host 'Portable deployment verification passed.' -ForegroundColor Green
