#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'


function Get-ArtifexSettings {
    param([Parameter(Mandatory=$true)][string]$Root)
    $settingsPath = Join-Path $Root 'config\settings.json'
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
        throw "Artifex settings not found: '$settingsPath'."
    }
    return (Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Resolve-ArtifexConfiguredPath {
    param(
        [Parameter(Mandatory=$true)][string]$Root,
        [Parameter(Mandatory=$true)][string]$Value
    )
    $expanded = $Value.Replace('${APP_ROOT}', $Root)
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $Root $expanded))
}

function Get-ArtifexPortablePaths {
    param([Parameter(Mandatory=$true)][string]$Root)
    $settings = Get-ArtifexSettings -Root $Root
    return [pscustomobject]@{
        Python = Resolve-ArtifexConfiguredPath -Root $Root -Value ([string]$settings.paths.runtimePython)
        Cli = Resolve-ArtifexConfiguredPath -Root $Root -Value ([string]$settings.paths.cliEntryPoint)
        CacheRoot = Resolve-ArtifexConfiguredPath -Root $Root -Value ([string]$settings.paths.cacheRoot)
        HuggingFaceCache = Resolve-ArtifexConfiguredPath -Root $Root -Value ([string]$settings.paths.modelCacheDirectory)
        TorchCache = Resolve-ArtifexConfiguredPath -Root $Root -Value ([string]$settings.paths.torchCacheDirectory)
    }
}

function Quote-QwenArgument {
    param([Parameter(Mandatory=$true)][AllowEmptyString()][string]$Value)
    if ($Value -match '^[A-Za-z0-9_./:\\=@+-]+$') { return $Value }
    return "'" + ($Value -replace "'", "''") + "'"
}

function Format-QwenCommand {
    param(
        [Parameter(Mandatory=$true)][string]$PythonPath,
        [Parameter(Mandatory=$true)][string[]]$Arguments
    )
    $parts = @((Quote-QwenArgument $PythonPath))
    foreach ($arg in $Arguments) { $parts += (Quote-QwenArgument ([string]$arg)) }
    return ($parts -join ' ')
}

function Add-QwenValueArgument {
    param(
        [Parameter(Mandatory=$true)][System.Collections.Generic.List[string]]$Arguments,
        [Parameter(Mandatory=$true)][System.Collections.IDictionary]$Bound,
        [Parameter(Mandatory=$true)][string]$ParameterName,
        [Parameter(Mandatory=$true)][string]$CliName
    )
    if ($Bound.Keys -contains $ParameterName) {
        $Arguments.Add($CliName)
        $Arguments.Add([string]$Bound[$ParameterName])
    }
}

function Add-QwenSwitchArgument {
    param(
        [Parameter(Mandatory=$true)][System.Collections.Generic.List[string]]$Arguments,
        [Parameter(Mandatory=$true)][System.Collections.IDictionary]$Bound,
        [Parameter(Mandatory=$true)][string]$ParameterName,
        [Parameter(Mandatory=$true)][string]$CliName
    )
    if (($Bound.Keys -contains $ParameterName) -and [bool]$Bound[$ParameterName]) { $Arguments.Add($CliName) }
}

function Invoke-QwenArchiveTask {
    param(
        [Parameter(Mandatory=$true)][ValidateSet('prompts','organize')][string]$Task,
        [Parameter(Mandatory=$true)][System.Collections.IDictionary]$BoundParameters,
        [Parameter(Mandatory=$true)][string]$InputPath,
        [Parameter(Mandatory=$true)][string]$PythonPath,
        [Parameter(Mandatory=$true)][string]$CliPath,
        [Parameter(Mandatory=$true)][string]$AppRoot,
        [Parameter(Mandatory=$true)][switch]$DryRun
    )

    if (($BoundParameters.Keys -contains 'PreferCommentsPrompt') -and ($BoundParameters.Keys -contains 'NoPreferCommentsPrompt')) {
        throw 'Use either -PreferCommentsPrompt or -NoPreferCommentsPrompt, not both.'
    }
    if (($BoundParameters.Keys -contains 'DefaultCaptureMetadata') -and ($BoundParameters.Keys -contains 'NoDefaultCaptureMetadata')) {
        throw 'Use either -DefaultCaptureMetadata or -NoDefaultCaptureMetadata, not both.'
    }
    if (($BoundParameters.Keys -contains 'AnalyzeMissing') -and ($BoundParameters.Keys -contains 'NoAnalyzeMissing')) {
        throw 'Use either -AnalyzeMissing or -NoAnalyzeMissing, not both.'
    }
    if (($BoundParameters.Keys -contains 'InferenceCache') -and ($BoundParameters.Keys -contains 'NoInferenceCache')) {
        throw 'Use either -InferenceCache or -NoInferenceCache, not both.'
    }
    if (($BoundParameters.Keys -contains 'AdaptiveGeneration') -and ($BoundParameters.Keys -contains 'NoAdaptiveGeneration')) {
        throw 'Use either -AdaptiveGeneration or -NoAdaptiveGeneration, not both.'
    }
    if (($BoundParameters.Keys -contains 'Color') -and ($BoundParameters.Keys -contains 'NoColor')) {
        throw 'Use either -Color or -NoColor, not both.'
    }

    $resolvedCli = [System.IO.Path]::GetFullPath($CliPath)
    if (-not $DryRun -and -not (Test-Path -LiteralPath $resolvedCli -PathType Leaf)) {
        throw "CLI entry point not found: '$resolvedCli'."
    }

    $args = [System.Collections.Generic.List[string]]::new()
    $args.Add($resolvedCli)
    $args.Add($Task)
    $args.Add('--input')
    $args.Add($InputPath)

    Add-QwenValueArgument $args $BoundParameters 'Config' '--config'
    Add-QwenSwitchArgument $args $BoundParameters 'Recurse' '--recursive'
    Add-QwenValueArgument $args $BoundParameters 'MaxFiles' '--max-files'
    Add-QwenValueArgument $args $BoundParameters 'Model' '--model'
    Add-QwenValueArgument $args $BoundParameters 'ModelCacheDir' '--model-cache-dir'
    Add-QwenSwitchArgument $args $BoundParameters 'LocalFilesOnly' '--local-files-only'
    Add-QwenValueArgument $args $BoundParameters 'Device' '--device'
    Add-QwenValueArgument $args $BoundParameters 'Precision' '--precision'
    Add-QwenValueArgument $args $BoundParameters 'Attention' '--attention'
    Add-QwenValueArgument $args $BoundParameters 'Quantization' '--quantization'
    Add-QwenValueArgument $args $BoundParameters 'LlmInt8Threshold' '--llm-int8-threshold'
    Add-QwenSwitchArgument $args $BoundParameters 'CompileModel' '--compile-model'
    Add-QwenValueArgument $args $BoundParameters 'MaxNewTokens' '--max-new-tokens'
    Add-QwenValueArgument $args $BoundParameters 'MaxInferenceSeconds' '--max-inference-seconds'
    Add-QwenValueArgument $args $BoundParameters 'MaxImageSide' '--max-image-side'
    Add-QwenSwitchArgument $args $BoundParameters 'AdaptiveGeneration' '--adaptive-generation'
    Add-QwenSwitchArgument $args $BoundParameters 'NoAdaptiveGeneration' '--no-adaptive-generation'
    Add-QwenValueArgument $args $BoundParameters 'MaxNewTokensCeiling' '--max-new-tokens-ceiling'
    Add-QwenValueArgument $args $BoundParameters 'TokenGrowthFactor' '--token-growth-factor'
    Add-QwenValueArgument $args $BoundParameters 'MinimumImageSide' '--minimum-image-side'
    Add-QwenValueArgument $args $BoundParameters 'ImageReductionFactor' '--image-reduction-factor'
    Add-QwenValueArgument $args $BoundParameters 'Temperature' '--temperature'
    Add-QwenValueArgument $args $BoundParameters 'TopP' '--top-p'
    Add-QwenValueArgument $args $BoundParameters 'TopK' '--top-k'
    Add-QwenSwitchArgument $args $BoundParameters 'Sampling' '--sampling'
    Add-QwenValueArgument $args $BoundParameters 'Seed' '--seed'
    Add-QwenValueArgument $args $BoundParameters 'BatchSize' '--batch-size'
    Add-QwenValueArgument $args $BoundParameters 'Delay' '--delay'
    Add-QwenValueArgument $args $BoundParameters 'Retries' '--retries'
    Add-QwenValueArgument $args $BoundParameters 'RetryDelaySeconds' '--retry-delay-seconds'
    Add-QwenValueArgument $args $BoundParameters 'Hint' '--hint'
    Add-QwenValueArgument $args $BoundParameters 'Prompt' '--prompt'
    Add-QwenSwitchArgument $args $BoundParameters 'PreferCommentsPrompt' '--prefer-comments-prompt'
    Add-QwenSwitchArgument $args $BoundParameters 'NoPreferCommentsPrompt' '--no-prefer-comments-prompt'
    Add-QwenSwitchArgument $args $BoundParameters 'DefaultCaptureMetadata' '--default-capture-metadata'
    Add-QwenSwitchArgument $args $BoundParameters 'NoDefaultCaptureMetadata' '--no-default-capture-metadata'
    Add-QwenSwitchArgument $args $BoundParameters 'Force' '--force'
    Add-QwenValueArgument $args $BoundParameters 'OutputRoot' '--output-root'
    Add-QwenValueArgument $args $BoundParameters 'BucketThreshold' '--bucket-threshold'
    Add-QwenValueArgument $args $BoundParameters 'BucketSize' '--bucket-size'
    Add-QwenValueArgument $args $BoundParameters 'MinConfidence' '--min-confidence'
    Add-QwenSwitchArgument $args $BoundParameters 'AnalyzeMissing' '--analyze-missing'
    Add-QwenSwitchArgument $args $BoundParameters 'NoAnalyzeMissing' '--no-analyze-missing'
    Add-QwenValueArgument $args $BoundParameters 'CategoriesFile' '--categories-file'
    Add-QwenSwitchArgument $args $BoundParameters 'InferenceCache' '--inference-cache'
    Add-QwenSwitchArgument $args $BoundParameters 'NoInferenceCache' '--no-inference-cache'
    Add-QwenValueArgument $args $BoundParameters 'CacheFile' '--cache-file'
    Add-QwenValueArgument $args $BoundParameters 'StateFile' '--state-file'
    Add-QwenValueArgument $args $BoundParameters 'ExifToolPath' '--exiftool-path'
    Add-QwenValueArgument $args $BoundParameters 'MetadataAttempts' '--metadata-attempts'
    Add-QwenValueArgument $args $BoundParameters 'MetadataTimeoutSeconds' '--metadata-timeout-seconds'
    Add-QwenSwitchArgument $args $BoundParameters 'Preview' '--preview'
    Add-QwenValueArgument $args $BoundParameters 'LogLevel' '--log-level'
    Add-QwenValueArgument $args $BoundParameters 'LogPath' '--log-path'
    Add-QwenSwitchArgument $args $BoundParameters 'Color' '--color'
    Add-QwenSwitchArgument $args $BoundParameters 'NoColor' '--no-color'

    $command = Format-QwenCommand -PythonPath $PythonPath -Arguments $args.ToArray()
    if ($DryRun) {
        # Host output is intentional: callers capture only the numeric return code.
        Write-Host $command
        return 0
    }

    # Keep all third-party caches inside paths defined by config\settings.json.
    $portablePaths = Get-ArtifexPortablePaths -Root $AppRoot
    New-Item -ItemType Directory -Force -Path $portablePaths.HuggingFaceCache, $portablePaths.TorchCache | Out-Null
    $env:HF_HOME = $portablePaths.HuggingFaceCache
    $env:HF_HUB_CACHE = (Join-Path $portablePaths.HuggingFaceCache 'hub')
    $env:TORCH_HOME = $portablePaths.TorchCache
    $env:XDG_CACHE_HOME = $portablePaths.CacheRoot
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONNOUSERSITE = '1'

    # Consume native stdout into the host so it is displayed live but is not
    # accidentally captured together with the numeric exit code by the wrapper.
    & $PythonPath @($args.ToArray()) | Out-Host
    $nativeExitCode = $LASTEXITCODE
    return $nativeExitCode
}
