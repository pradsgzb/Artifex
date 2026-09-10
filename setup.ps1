#requires -Version 5.1
[CmdletBinding()]
param(
    [AllowEmptyString()][string]$TorchBuild = '',
    [switch]$SkipModelDownload,
    [switch]$ForceRuntime,
    [switch]$ForceDependencies,
    [switch]$ForceModel,
    [switch]$ForceExifTool,
    [switch]$NoComfyUIReuse,
    [AllowEmptyString()][string]$ComfyUIWeightsFile = '',
    [switch]$KeepSetupCache,
    [switch]$KeepLegacyVenv
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ($env:OS -ne 'Windows_NT') {
    throw 'This bootstrap package currently supports Windows x64 only.'
}

$root = $PSScriptRoot
$settingsPath = Join-Path $root 'config\settings.json'
$deploymentConfigPath = Join-Path $root 'config\deployment.json'
foreach ($requiredConfig in @($settingsPath, $deploymentConfigPath)) {
    if (-not (Test-Path -LiteralPath $requiredConfig -PathType Leaf)) {
        throw "Required configuration not found: $requiredConfig"
    }
}
$settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$deployment = Get-Content -LiteralPath $deploymentConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($TorchBuild)) {
    $TorchBuild = [string]$settings.bootstrap.torchBuild
}
if (@('cu128','cu126','cpu') -notcontains $TorchBuild) {
    throw "Invalid Torch build '$TorchBuild'. Expected one of: cu128, cu126, cpu."
}

function Resolve-ArtifexConfiguredPath {
    param([Parameter(Mandatory=$true)][string]$Value)
    $expanded = $Value.Replace('${APP_ROOT}', $root)
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $root $expanded))
}

$pythonExe = Resolve-ArtifexConfiguredPath ([string]$settings.paths.runtimePython)
$pythonRoot = Split-Path -Parent $pythonExe
$runtimeRoot = Split-Path -Parent $pythonRoot
$modelRoot = Resolve-ArtifexConfiguredPath ([string]$settings.paths.modelDirectory)
$modelsRoot = Split-Path -Parent $modelRoot
$exifExe = Resolve-ArtifexConfiguredPath ([string]$settings.paths.exifToolExecutable)
$exifRoot = Split-Path -Parent $exifExe
$toolsRoot = Split-Path -Parent $exifRoot
$cacheRoot = Resolve-ArtifexConfiguredPath ([string]$settings.paths.cacheRoot)
$setupCache = Resolve-ArtifexConfiguredPath ([string]$settings.paths.setupCacheDirectory)
$hfCache = Resolve-ArtifexConfiguredPath ([string]$settings.paths.modelCacheDirectory)
$pipCache = Resolve-ArtifexConfiguredPath ([string]$settings.paths.pipCacheDirectory)
$torchCache = Resolve-ArtifexConfiguredPath ([string]$settings.paths.torchCacheDirectory)
$logsRoot = Resolve-ArtifexConfiguredPath ([string]$settings.paths.logsDirectory)
$manifestRoot = Resolve-ArtifexConfiguredPath ([string]$settings.paths.deploymentStateDirectory)
$cliEntryPoint = Resolve-ArtifexConfiguredPath ([string]$settings.paths.cliEntryPoint)

New-Item -ItemType Directory -Force -Path @(
    $runtimeRoot, $modelsRoot, $toolsRoot, $cacheRoot, $setupCache,
    $hfCache, $pipCache, $logsRoot, $manifestRoot
) | Out-Null

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory=$true)][string]$Executable,
        [Parameter(Mandatory=$true)][AllowEmptyCollection()][string[]]$Arguments,
        [Parameter(Mandatory=$true)][string]$Description
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Test-Sha256 {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Expected
    )
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA-256 mismatch for '$Path'. Expected $Expected but received $actual."
    }
}

function Test-ZipSignature {
    param([Parameter(Mandatory=$true)][string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $first = $stream.ReadByte()
        $second = $stream.ReadByte()
    } finally {
        $stream.Dispose()
    }
    if ($first -ne 0x50 -or $second -ne 0x4B) {
        throw "Downloaded response is not a ZIP archive (missing PK signature). The server likely returned an HTML download page instead of the requested file."
    }
}

function Get-PortableDownloadCandidates {
    param([Parameter(Mandatory=$true)][string]$Url)

    $candidates = New-Object System.Collections.Generic.List[string]
    $match = [regex]::Match(
        $Url,
        '^https://(?:download|downloads)\.sourceforge\.net/project/([^/]+)/files/(.+?)(?:\?.*)?$'
    )
    if ($match.Success) {
        $project = $match.Groups[1].Value
        $relative = $match.Groups[2].Value
        # SourceForge serves browser-looking clients an HTML countdown page.
        # Its documented command-line URL redirects downloader clients to a mirror.
        $candidates.Add(('https://sourceforge.net/projects/{0}/files/{1}/download' -f $project, $relative))
        # Generic FRS mirror selector. Mirror URLs do not contain the UI-only /files/ segment.
        $candidates.Add(('https://downloads.sourceforge.net/project/{0}/{1}' -f $project, $relative))
    } else {
        $candidates.Add($Url)
    }
    return @($candidates | Select-Object -Unique)
}

function Get-PortableFile {
    param(
        [Parameter(Mandatory=$true)][string]$Url,
        [Parameter(Mandatory=$true)][string]$Destination,
        [AllowEmptyString()][string]$Sha256 = '',
        [switch]$ExpectedZip
    )
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        try {
            if ($ExpectedZip) { Test-ZipSignature -Path $Destination }
            if (-not [string]::IsNullOrWhiteSpace($Sha256)) {
                Test-Sha256 -Path $Destination -Expected $Sha256
            }
            return
        } catch {
            Remove-Item -LiteralPath $Destination -Force
        }
    }

    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temp = "$Destination.download"
    Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    $errors = @()
    $candidates = @(Get-PortableDownloadCandidates -Url $Url)
    $attemptsPerCandidate = if ($candidates.Count -gt 1) { 2 } else { 3 }

    foreach ($candidate in $candidates) {
        for ($attempt = 1; $attempt -le $attemptsPerCandidate; $attempt++) {
            try {
                Write-Host "Downloading $candidate" -ForegroundColor Cyan
                # A browser-like PowerShell User-Agent makes SourceForge return its
                # JavaScript/countdown HTML page. Wget identifies this as a CLI download.
                Invoke-WebRequest -Uri $candidate -OutFile $temp -UseBasicParsing -TimeoutSec 600 -UserAgent 'Wget/1.21.4'
                if ($ExpectedZip) { Test-ZipSignature -Path $temp }
                if (-not [string]::IsNullOrWhiteSpace($Sha256)) {
                    Test-Sha256 -Path $temp -Expected $Sha256
                }
                Move-Item -LiteralPath $temp -Destination $Destination -Force
                return
            } catch {
                $errors += ('{0} attempt {1}: {2}' -f $candidate, $attempt, $_.Exception.Message)
                Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
                if ($attempt -lt $attemptsPerCandidate) { Start-Sleep -Seconds 2 }
            }
        }
    }
    throw "Download failed for all candidate URLs: $Url :: $($errors -join ' | ')"
}

function Configure-EmbeddedPythonPaths {
    param([Parameter(Mandatory=$true)][string]$PythonDirectory)
    $pth = Get-ChildItem -LiteralPath $PythonDirectory -Filter 'python*._pth' -File | Select-Object -First 1
    if ($null -eq $pth) { throw "Embedded Python ._pth file not found under $PythonDirectory" }

    # The embeddable Windows runtime ignores the script/current directory while
    # a ._pth file is present.  runtime\python is two levels below the Artifex
    # root, so '..\..' makes the adjacent qwen_archive package importable after
    # the entire folder is copied to any drive/path.
    $requiredPaths = @('Lib\site-packages', '..\..')
    $lines = @(Get-Content -LiteralPath $pth.FullName -Encoding UTF8)
    $result = New-Object System.Collections.Generic.List[string]
    $seen = @{}
    $hasImportSite = $false

    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        foreach ($requiredPath in $requiredPaths) {
            if ($trimmed -ieq $requiredPath) { $seen[$requiredPath] = $true }
        }
        if ($trimmed -ieq 'import site') { $hasImportSite = $true }

        if ($trimmed -ieq '#import site') {
            foreach ($requiredPath in $requiredPaths) {
                if (-not $seen.ContainsKey($requiredPath)) {
                    $result.Add($requiredPath)
                    $seen[$requiredPath] = $true
                }
            }
            $result.Add('import site')
            $hasImportSite = $true
        } else {
            $result.Add($line)
        }
    }

    foreach ($requiredPath in $requiredPaths) {
        if (-not $seen.ContainsKey($requiredPath)) { $result.Add($requiredPath) }
    }
    if (-not $hasImportSite) { $result.Add('import site') }

    Set-Content -LiteralPath $pth.FullName -Value $result.ToArray() -Encoding ASCII
    New-Item -ItemType Directory -Force -Path (Join-Path $PythonDirectory 'Lib\site-packages') | Out-Null
}

# Keep every cache/download inside the application root.
$env:HF_HOME = $hfCache
$env:HF_HUB_CACHE = Join-Path $hfCache 'hub'
$env:TORCH_HOME = $torchCache
$env:XDG_CACHE_HOME = $cacheRoot
$env:PIP_CACHE_DIR = $pipCache
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONNOUSERSITE = '1'

# 1) Portable Python runtime.
$pythonZip = Join-Path $setupCache ("python-{0}-embed-amd64.zip" -f [string]$deployment.python.version)
if ($ForceRuntime -and (Test-Path -LiteralPath $pythonRoot)) {
    Remove-Item -LiteralPath $pythonRoot -Recurse -Force
}
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    Get-PortableFile -Url ([string]$deployment.python.url) -Destination $pythonZip -Sha256 ([string]$deployment.python.sha256) -ExpectedZip
    New-Item -ItemType Directory -Force -Path $pythonRoot | Out-Null
    Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonRoot -Force
}
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) { throw "Portable Python was not created: $pythonExe" }
# Run on every setup invocation as well as first install so an already-created
# portable runtime is repaired in-place when the bootstrap logic is upgraded.
Configure-EmbeddedPythonPaths -PythonDirectory $pythonRoot
Invoke-NativeChecked -Executable $pythonExe -Arguments @('--version') -Description 'Portable Python startup'

# 2) Local dependencies. pip itself runs as a downloaded zipapp only when an
# install/update is actually required; a completed Full Portable folder can be
# verified again without internet access.
$pipPyz = Join-Path $setupCache ("pip-{0}.pyz" -f [string]$deployment.pip.version)

$dependencyMarker = Join-Path $runtimeRoot ("dependencies-{0}.complete" -f $TorchBuild)
$dependencyFingerprint = @(
    $TorchBuild,
    (Get-FileHash -LiteralPath (Join-Path $root 'requirements.txt') -Algorithm SHA256).Hash,
    (Get-FileHash -LiteralPath (Join-Path $root 'requirements-server.txt') -Algorithm SHA256).Hash,
    (Get-FileHash -LiteralPath (Join-Path $root 'requirements-optional.txt') -Algorithm SHA256).Hash,
    (Get-FileHash -LiteralPath $deploymentConfigPath -Algorithm SHA256).Hash
) -join '|'
$dependenciesCurrent = $false
if (Test-Path -LiteralPath $dependencyMarker -PathType Leaf) {
    $dependenciesCurrent = ((Get-Content -LiteralPath $dependencyMarker -Raw -Encoding ASCII).Trim() -eq $dependencyFingerprint)
}
if ($ForceDependencies -or -not $dependenciesCurrent) {
    Get-PortableFile -Url ([string]$deployment.pip.url) -Destination $pipPyz
    Invoke-NativeChecked -Executable $pythonExe -Arguments @(
        $pipPyz, '--isolated', 'install', '--upgrade', '--disable-pip-version-check', '--no-warn-script-location',
        'setuptools==78.1.0', 'wheel==0.48.0', 'packaging==26.3'
    ) -Description 'Python packaging dependency installation'

    $torchIndex = "{0}/{1}" -f ([string]$deployment.torch.indexBase).TrimEnd('/'), $TorchBuild
    $torchPackage = "torch=={0}+{1}" -f [string]$deployment.torch.torchVersion, $TorchBuild
    $torchvisionPackage = "torchvision=={0}+{1}" -f [string]$deployment.torch.torchvisionVersion, $TorchBuild
    Invoke-NativeChecked -Executable $pythonExe -Arguments @(
        $pipPyz, '--isolated', 'install', '--upgrade', '--disable-pip-version-check', '--no-warn-script-location',
        $torchPackage, $torchvisionPackage, '--index-url', $torchIndex
    ) -Description 'PyTorch installation'

    Invoke-NativeChecked -Executable $pythonExe -Arguments @(
        $pipPyz, '--isolated', 'install', '--upgrade', '--disable-pip-version-check', '--no-warn-script-location',
        '-r', (Join-Path $root 'requirements.txt')
        '-r', (Join-Path $root 'requirements-server.txt')
    ) -Description 'Application dependency installation'

    if ($TorchBuild -ne 'cpu') {
        Invoke-NativeChecked -Executable $pythonExe -Arguments @(
            $pipPyz, '--isolated', 'install', '--upgrade', '--disable-pip-version-check', '--no-warn-script-location',
            '-r', (Join-Path $root 'requirements-optional.txt')
        ) -Description 'Quantization dependency installation'
    }
    Set-Content -LiteralPath $dependencyMarker -Value $dependencyFingerprint -Encoding ASCII
}

# Verify the installed runtime without requiring pip/network on completed deployments.
$dependencyCheckCode = @'
import importlib
import logging
import sys

logging.getLogger("torch.utils.flop_counter").setLevel(logging.ERROR)

modules = [
    "torch", "torchvision", "transformers", "accelerate",
    "huggingface_hub", "safetensors", "PIL", "numpy",
]
if sys.argv[1] != "cpu":
    modules.append("bitsandbytes")
for name in modules:
    importlib.import_module(name)
print("runtime_dependencies=OK")
'@
Invoke-NativeChecked -Executable $pythonExe -Arguments @('-c', $dependencyCheckCode, $TorchBuild) -Description 'Python dependency verification'

# 3) Portable ExifTool, including its companion exiftool_files directory.
$exifZip = Join-Path $setupCache ("exiftool-{0}_64.zip" -f [string]$deployment.exifTool.version)
if ($ForceExifTool -and (Test-Path -LiteralPath $exifRoot)) {
    Remove-Item -LiteralPath $exifRoot -Recurse -Force
}
if (-not (Test-Path -LiteralPath $exifExe -PathType Leaf)) {
    Get-PortableFile -Url ([string]$deployment.exifTool.url) -Destination $exifZip -Sha256 ([string]$deployment.exifTool.sha256) -ExpectedZip
    $exifExtract = Join-Path $setupCache 'exiftool-extracted'
    Remove-Item -LiteralPath $exifExtract -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $exifExtract | Out-Null
    Expand-Archive -LiteralPath $exifZip -DestinationPath $exifExtract -Force
    $exifSource = Get-ChildItem -LiteralPath $exifExtract -Directory | Select-Object -First 1
    if ($null -eq $exifSource) { throw 'ExifTool archive did not contain the expected top-level directory.' }
    New-Item -ItemType Directory -Force -Path $exifRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $exifSource.FullName 'exiftool(-k).exe') -Destination $exifExe -Force
    Copy-Item -LiteralPath (Join-Path $exifSource.FullName 'exiftool_files') -Destination (Join-Path $exifRoot 'exiftool_files') -Recurse -Force
    Remove-Item -LiteralPath $exifExtract -Recurse -Force
}
Invoke-NativeChecked -Executable $exifExe -Arguments @('-ver') -Description 'ExifTool verification'

# 4) Local model. Optional external model reuse is controlled entirely by
# config\settings.json. Relative external paths are resolved from the Artifex root.
if (-not $SkipModelDownload) {
    $effectiveComfy = ''
    if (-not $NoComfyUIReuse) {
        $configuredComfy = $ComfyUIWeightsFile
        if ([string]::IsNullOrWhiteSpace($configuredComfy) -and [bool]$settings.externalSources.comfyUI.enabled) {
            $comfyDir = Resolve-ArtifexConfiguredPath ([string]$settings.externalSources.comfyUI.textEncodersDirectory)
            $configuredComfy = Join-Path $comfyDir ([string]$settings.externalSources.comfyUI.qwenWeightFile)
        }
        if (-not [string]::IsNullOrWhiteSpace($configuredComfy)) {
            $resolvedComfy = Resolve-ArtifexConfiguredPath $configuredComfy
            if (Test-Path -LiteralPath $resolvedComfy -PathType Leaf) {
                $effectiveComfy = (Resolve-Path -LiteralPath $resolvedComfy).Path
            }
        }
    }

    $prepareModelCode = @'
import os
import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download
from safetensors import safe_open
from transformers import AutoConfig, AutoProcessor

repo_id = sys.argv[1]
revision = sys.argv[2]
local_dir = Path(sys.argv[3]).resolve()
cache_dir = Path(sys.argv[4]).resolve()
comfy_raw = sys.argv[5].strip()
force = sys.argv[6] == "1"
comfy_weights = Path(comfy_raw).resolve() if comfy_raw else None

local_dir.mkdir(parents=True, exist_ok=True)
cache_dir.mkdir(parents=True, exist_ok=True)

support_files = [
    "config.json",
    "generation_config.json",
    "preprocessor_config.json",
    "video_preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "chat_template.jinja",
]


def complete_local_model() -> bool:
    required = ["config.json", "tokenizer_config.json"]
    if not all((local_dir / name).is_file() for name in required):
        return False
    return (local_dir / "model.safetensors").is_file() or (local_dir / "model.safetensors.index.json").is_file()


def validate_local_model() -> None:
    AutoConfig.from_pretrained(str(local_dir), local_files_only=True)
    AutoProcessor.from_pretrained(str(local_dir), local_files_only=True)
    single = local_dir / "model.safetensors"
    if single.is_file():
        with safe_open(str(single), framework="pt", device="cpu") as handle:
            keys = list(handle.keys())
        if not any(key.startswith("model.language_model.") for key in keys):
            raise RuntimeError("Portable Qwen weight does not expose expected language-model tensors.")
        if not any(key.startswith("model.visual.") for key in keys):
            raise RuntimeError("Portable Qwen weight does not contain the visual tower required for image analysis.")


if complete_local_model() and not force:
    validate_local_model()
    print(f"Existing portable model is complete: {local_dir}")
else:
    if force and local_dir.exists():
        for child in local_dir.iterdir():
            if child.name == ".cache":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

    if comfy_weights and comfy_weights.is_file():
        print(f"Copying existing ComfyUI Qwen weight into portable model folder: {comfy_weights}")
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=str(local_dir),
            allow_patterns=support_files,
        )
        for shard in local_dir.glob("model-*.safetensors"):
            shard.unlink(missing_ok=True)
        (local_dir / "model.safetensors.index.json").unlink(missing_ok=True)
        target = local_dir / "model.safetensors"
        if not target.is_file() or os.path.getsize(target) != os.path.getsize(comfy_weights):
            shutil.copy2(comfy_weights, target)
    else:
        print(f"Downloading complete Qwen model into portable folder: {local_dir}")
        (local_dir / "model.safetensors").unlink(missing_ok=True)
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=str(local_dir),
        )

    shutil.rmtree(local_dir / ".cache", ignore_errors=True)
    validate_local_model()
    print(f"Portable Transformers model ready: {local_dir}")
'@
    Invoke-NativeChecked -Executable $pythonExe -Arguments @(
        '-c', $prepareModelCode,
        [string]$deployment.model.repoId,
        [string]$deployment.model.revision,
        $modelRoot,
        $hfCache,
        $effectiveComfy,
        ($(if ($ForceModel) { '1' } else { '0' }))
    ) -Description 'Portable Qwen model preparation'
}

# 5) Final offline/runtime verification and portable manifest.
$verifyCode = @'
import json
import sys
from pathlib import Path

import torch
import transformers
from transformers import AutoConfig, AutoProcessor

root = Path(sys.argv[1]).resolve()
model = Path(sys.argv[2]).resolve()
print("python=", sys.version.split()[0])
print("transformers=", transformers.__version__)
print("torch=", torch.__version__)
print("cuda_runtime=", torch.version.cuda)
print("cuda=", torch.cuda.is_available())
if torch.cuda.is_available():
    p = torch.cuda.get_device_properties(0)
    print("gpu=", p.name)
    print("vram_gb=", round(p.total_memory / (1024 ** 3), 2))
else:
    print("gpu= CPU")
    print("vram_gb= 0")
try:
    import bitsandbytes as bnb
    print("bitsandbytes=", bnb.__version__)
except Exception as exc:
    print("bitsandbytes= unavailable:", exc)
if model.exists():
    AutoConfig.from_pretrained(str(model), local_files_only=True)
    AutoProcessor.from_pretrained(str(model), local_files_only=True)
    print("model_offline= OK")
else:
    print("model_offline= SKIPPED")
'@
Invoke-NativeChecked -Executable $pythonExe -Arguments @('-c', $verifyCode, $root, $modelRoot) -Description 'Portable runtime verification'

if ($TorchBuild -ne 'cpu') {
    $cudaAvailable = & $pythonExe -c "import torch; print('1' if torch.cuda.is_available() else '0')"
    if (($cudaAvailable | Select-Object -Last 1).Trim() -ne '1') {
        throw 'CUDA PyTorch was installed, but torch.cuda.is_available() is False. A compatible NVIDIA driver is required on this machine.'
    }
}

# Persist the selected deployment profile into the unified user settings file.
if ($TorchBuild -eq 'cpu') {
    $settings.application.device = 'cpu'
    $settings.application.precision = 'float32'
    $settings.application.quantization = 'none'
} else {
    $settings.application.device = 'cuda'
    $settings.application.precision = 'float16'
    $settings.application.quantization = '8bit'
}
$settings | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $settingsPath -Encoding UTF8

$manifest = [ordered]@{
    schemaVersion = 1
    createdAt = (Get-Date).ToString('o')
    platform = 'windows-x64'
    torchBuild = $TorchBuild
    python = [string]$settings.paths.runtimePython
    model = [string]$settings.paths.modelDirectory
    exifTool = [string]$settings.paths.exifToolExecutable
    portable = $true
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $manifestRoot 'installed.json') -Encoding UTF8

$legacyVenv = Join-Path $root '.venv'
if (-not $KeepLegacyVenv -and (Test-Path -LiteralPath $legacyVenv -PathType Container)) {
    Write-Host "Removing legacy machine-specific virtual environment: $legacyVenv" -ForegroundColor DarkGray
    Remove-Item -LiteralPath $legacyVenv -Recurse -Force
}

if (-not $KeepSetupCache) {
    # A Full Portable deployment needs the installed runtime/model/tool files, not
    # the multi-gigabyte download caches used to build them. Remove build caches
    # and recreate empty cache directories for normal runtime use.
    foreach ($dir in @($setupCache, $pipCache, $hfCache, $torchCache)) {
        Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

Write-Host ''
Write-Host 'Portable setup complete.' -ForegroundColor Green
Write-Host "Python:   $pythonExe" -ForegroundColor Cyan
Write-Host "Model:    $modelRoot" -ForegroundColor Cyan
Write-Host "ExifTool: $exifExe" -ForegroundColor Cyan
Write-Host 'This root folder may now be copied to another Windows x64 machine with a compatible NVIDIA driver.' -ForegroundColor Green
