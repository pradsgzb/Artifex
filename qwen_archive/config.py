"""Portable JSON configuration loading and validation."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Mapping

from .constants import APP_NAME, DEFAULT_CONFIG_PATH, PACKAGE_ROOT
from .errors import ConfigurationError
from .settings import (
    AdaptiveGenerationPolicy,
    LoggingSettings,
    ServerSettings,
    require_bool,
    require_float,
    require_int,
    require_safe_directory_name,
)

APP_ROOT_TOKEN = "${APP_ROOT}"
PATH_TOKEN_MAP = {
    "${MODEL_DIRECTORY}": "modelDirectory",
    "${MODEL_CACHE_DIRECTORY}": "modelCacheDirectory",
    "${EXIFTOOL_EXECUTABLE}": "exifToolExecutable",
    "${RUNTIME_PYTHON}": "runtimePython",
    "${CACHE_ROOT}": "cacheRoot",
    "${LOGS_DIRECTORY}": "logsDirectory",
    "${WEB_DIST_DIRECTORY}": "webDistDirectory",
    "${WEB_STATIC_DIRECTORY}": "webStaticDirectory",
}


def _platform_key() -> str:
    if os.name == "nt":
        return "windows"
    if platform.system().casefold() == "darwin":
        return "macos"
    return "linux"


def _configuration_root(settings_path: Path) -> Path:
    # Canonical settings live in <root>/config/settings.json. Custom files use
    # their containing directory only when they are not under a config folder.
    parent = settings_path.parent
    return parent.parent if parent.name.casefold() == "config" else parent


def _resolve_root_relative_path(value: str, *, root: Path) -> str:
    expanded = value.replace(APP_ROOT_TOKEN, str(root))
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = root / path
    return str(path.resolve())


def _normalized_paths(settings: Mapping[str, Any], *, root: Path) -> dict[str, str]:
    raw_paths = settings.get("paths", {})
    if not isinstance(raw_paths, Mapping):
        raise ConfigurationError("Configuration 'paths' must be a JSON object.")
    merged: dict[str, Any] = dict(raw_paths)
    overrides = settings.get("platformPaths", {})
    if overrides is not None and not isinstance(overrides, Mapping):
        raise ConfigurationError("Configuration 'platformPaths' must be a JSON object.")
    platform_values = overrides.get(_platform_key(), {}) if isinstance(overrides, Mapping) else {}
    if platform_values is not None and not isinstance(platform_values, Mapping):
        raise ConfigurationError(f"Configuration platformPaths.{_platform_key()} must be a JSON object.")
    if isinstance(platform_values, Mapping):
        merged.update(platform_values)

    result: dict[str, str] = {}
    for key, value in merged.items():
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"Configuration path '{key}' must be a non-empty string.")
        cleaned = value.strip()
        # A bare executable name intentionally resolves through PATH. Other
        # configured values are application-root-relative unless absolute.
        if (
            str(key) == "exifToolExecutable"
            and APP_ROOT_TOKEN not in cleaned
            and not Path(cleaned).is_absolute()
            and len(Path(cleaned).parts) == 1
        ):
            result[str(key)] = cleaned
        else:
            result[str(key)] = _resolve_root_relative_path(cleaned, root=root)
    return result


def _expand_setting_tokens(value: Any, paths: Mapping[str, str], *, root: Path) -> Any:
    if isinstance(value, str):
        expanded = value.replace(APP_ROOT_TOKEN, str(root))
        for token, key in PATH_TOKEN_MAP.items():
            if token in expanded:
                if key not in paths:
                    raise ConfigurationError(f"Configuration uses {token}, but paths.{key} is not defined.")
                expanded = expanded.replace(token, paths[key])
        return expanded
    if isinstance(value, list):
        return [_expand_setting_tokens(item, paths, root=root) for item in value]
    if isinstance(value, dict):
        return {key: _expand_setting_tokens(item, paths, root=root) for key, item in value.items()}
    return value


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON configuration {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Configuration root must be a JSON object: {path}")
    return data


def load_settings(path: Path | str | None = None) -> dict[str, Any]:
    settings_path = Path(path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    data = _read_json(settings_path)
    root = _configuration_root(settings_path)
    paths = _normalized_paths(data, root=root)
    result = _expand_setting_tokens(dict(data), paths, root=root)
    result["_resolvedPaths"] = paths
    result["_configPath"] = str(settings_path)
    result["_appRoot"] = str(root)
    return result


def validate_application_config(app: Mapping[str, Any], *, resolved_paths: Mapping[str, str]) -> None:
    """Fail fast for dangerous configuration coercions and contradictions."""
    boolean_defaults = {
        "localFilesOnly": False,
        "compileModel": False,
        "sampling": False,
        "preferCommentsPrompt": True,
        "defaultCaptureMetadata": True,
        "analyzeMissing": True,
        "inferenceCache": True,
    }
    for key, default in boolean_defaults.items():
        require_bool(app.get(key, default), f"application.{key}")

    max_tokens = require_int(app.get("maxNewTokens", 1024), "application.maxNewTokens", minimum=32, maximum=131072)
    max_side = require_int(app.get("maxImageSide", 1024), "application.maxImageSide", minimum=0, maximum=32768)
    require_int(app.get("retries", 2), "application.retries", minimum=0, maximum=10)
    require_float(app.get("retryDelaySeconds", 1.0), "application.retryDelaySeconds", minimum=0.0, maximum=3600.0)
    require_float(app.get("maxInferenceSeconds", 180.0), "application.maxInferenceSeconds", minimum=0.0, maximum=86400.0)
    require_safe_directory_name(app.get("stateDirectoryName", "states"), "application.stateDirectoryName")

    adaptive = app.get("adaptiveGeneration", {})
    if adaptive is not None and not isinstance(adaptive, Mapping):
        raise ConfigurationError("application.adaptiveGeneration must be a JSON object.")
    AdaptiveGenerationPolicy.from_mapping(
        adaptive if isinstance(adaptive, Mapping) else {},
        initial_max_new_tokens=max_tokens,
        initial_max_image_side=max_side,
    )

    folder = app.get("folderClassification")
    if not isinstance(folder, Mapping):
        raise ConfigurationError("application.folderClassification must be a JSON object.")
    min_levels = require_int(folder.get("minLevels"), "application.folderClassification.minLevels", minimum=1, maximum=32)
    max_levels = require_int(folder.get("maxLevels"), "application.folderClassification.maxLevels", minimum=1, maximum=32)
    if max_levels < min_levels:
        raise ConfigurationError("application.folderClassification.maxLevels must be >= minLevels.")
    for key in ("failOnProhibitedFolder", "failOnLowConfidence", "failOnNeedsReview"):
        require_bool(folder.get(key), f"application.folderClassification.{key}")

    capture_values = app.get("defaultCaptureMetadataValues", {})
    if not isinstance(capture_values, Mapping):
        raise ConfigurationError("application.defaultCaptureMetadataValues must be a JSON object.")
    for key, value in capture_values.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, (str, int, float)):
            raise ConfigurationError("application.defaultCaptureMetadataValues must map tag names to scalar values.")

    LoggingSettings.from_application(app)
    server = app.get("server", {})
    if server is not None and not isinstance(server, Mapping):
        raise ConfigurationError("application.server must be a JSON object.")
    ServerSettings.from_mapping(server if isinstance(server, Mapping) else {}, resolved_paths=resolved_paths)


def load_json_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load the canonical settings file or a legacy flat application JSON."""
    config_path = Path(path or DEFAULT_CONFIG_PATH).expanduser().resolve()
    data = _read_json(config_path)
    root = _configuration_root(config_path)

    if isinstance(data.get("application"), dict):
        paths = _normalized_paths(data, root=root)
        app = dict(data["application"])
    else:
        # Legacy flat files inherit portable path definitions from canonical settings.
        settings = load_settings()
        paths = settings["_resolvedPaths"]
        root = Path(settings["_appRoot"])
        app = dict(data)

    app = _expand_setting_tokens(app, paths, root=root)
    app["_configPath"] = str(config_path)
    app["_appRoot"] = str(root)
    app["_resolvedPaths"] = paths
    validate_application_config(app, resolved_paths=paths)
    return app


def derive_managed_root(input_path: Path) -> Path:
    resolved = input_path.expanduser().resolve()
    base = resolved.parent if resolved.is_file() else resolved
    if os.name == "nt":
        anchor = Path(base.anchor)
        relative = base.relative_to(anchor)
        return anchor if not relative.parts else anchor / relative.parts[0]
    return base


def default_state_dir(
    input_path: Path,
    *,
    state_directory_name: str = "states",
    application_name: str = APP_NAME,
) -> Path:
    state_segment = require_safe_directory_name(state_directory_name, "application.stateDirectoryName")
    app_segment = require_safe_directory_name(application_name, "applicationName")
    return derive_managed_root(input_path) / state_segment / app_segment
