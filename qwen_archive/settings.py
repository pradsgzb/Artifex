"""Strict, typed settings used by the inference and HTTP layers."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .errors import ConfigurationError

_SAFE_SEGMENT = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f.][^<>:\"/\\|?*\x00-\x1f]*$")


def require_bool(value: Any, path: str) -> bool:
    """Return a real JSON boolean; never treat strings such as ``"false"`` as true."""
    if type(value) is not bool:  # bool is intentionally exact; ints are rejected.
        raise ConfigurationError(f"{path} must be a JSON boolean (true or false).")
    return value


def require_int(value: Any, path: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is bool or not isinstance(value, int):
        raise ConfigurationError(f"{path} must be an integer.")
    if minimum is not None and value < minimum:
        raise ConfigurationError(f"{path} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ConfigurationError(f"{path} must be <= {maximum}.")
    return value


def require_float(
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{path} must be numeric.")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ConfigurationError(f"{path} must be >= {minimum}.")
    if maximum is not None and result > maximum:
        raise ConfigurationError(f"{path} must be <= {maximum}.")
    return result


def require_str(value: Any, path: str, *, non_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{path} must be a string.")
    result = value.strip()
    if non_empty and not result:
        raise ConfigurationError(f"{path} must not be empty.")
    return result


def require_safe_directory_name(value: Any, path: str) -> str:
    result = require_str(value, path)
    if result in {".", ".."} or not _SAFE_SEGMENT.fullmatch(result) or result.endswith((" ", ".")):
        raise ConfigurationError(f"{path} must be one safe directory-name segment.")
    return result


@dataclass(frozen=True)
class AdaptiveGenerationPolicy:
    enabled: bool = True
    max_new_tokens_ceiling: int = 3072
    token_growth_factor: float = 1.6
    minimum_image_side: int = 640
    image_reduction_factor: float = 0.75

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
        *,
        initial_max_new_tokens: int,
        initial_max_image_side: int,
    ) -> "AdaptiveGenerationPolicy":
        data = dict(value or {})
        policy = cls(
            enabled=require_bool(data.get("enabled", True), "application.adaptiveGeneration.enabled"),
            max_new_tokens_ceiling=require_int(
                data.get("maxNewTokensCeiling", max(3072, initial_max_new_tokens)),
                "application.adaptiveGeneration.maxNewTokensCeiling",
                minimum=32,
                maximum=131072,
            ),
            token_growth_factor=require_float(
                data.get("tokenGrowthFactor", 1.6),
                "application.adaptiveGeneration.tokenGrowthFactor",
                minimum=1.01,
                maximum=8.0,
            ),
            minimum_image_side=require_int(
                data.get("minimumImageSide", 640 if initial_max_image_side <= 0 else min(640, initial_max_image_side)),
                "application.adaptiveGeneration.minimumImageSide",
                minimum=64,
                maximum=32768,
            ),
            image_reduction_factor=require_float(
                data.get("imageReductionFactor", 0.75),
                "application.adaptiveGeneration.imageReductionFactor",
                minimum=0.1,
                maximum=0.99,
            ),
        )
        if policy.max_new_tokens_ceiling < initial_max_new_tokens:
            raise ConfigurationError(
                "application.adaptiveGeneration.maxNewTokensCeiling must be >= application.maxNewTokens."
            )
        if initial_max_image_side > 0 and policy.minimum_image_side > initial_max_image_side:
            raise ConfigurationError(
                "application.adaptiveGeneration.minimumImageSide must be <= application.maxImageSide."
            )
        return policy

    def next_token_budget(self, current: int) -> int:
        if not self.enabled:
            return current
        grown = max(current + 1, int(round(current * self.token_growth_factor)))
        return min(self.max_new_tokens_ceiling, grown)

    def next_image_side(self, current: int) -> int:
        if not self.enabled or current <= 0:
            return current
        reduced = int(round(current * self.image_reduction_factor))
        return max(self.minimum_image_side, min(current, reduced))


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    color: str = "auto"
    path: str = ""

    @classmethod
    def from_application(cls, application: Mapping[str, Any]) -> "LoggingSettings":
        nested = application.get("logging")
        data = dict(nested) if isinstance(nested, Mapping) else {}
        level = str(data.get("level", application.get("logLevel", "INFO"))).upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("application.logging.level is invalid.")
        color = str(data.get("color", application.get("logColor", "auto"))).lower()
        if color not in {"auto", "always", "never"}:
            raise ConfigurationError("application.logging.color must be auto, always, or never.")
        path = str(data.get("path", application.get("logPath", ""))).strip()
        return cls(level=level, color=color, path=path)


@dataclass(frozen=True)
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8000
    preload_model: bool = False
    api_key_environment_variable: str = "ARTIFEX_API_KEY"
    allow_unauthenticated_remote: bool = False
    cors_origins: tuple[str, ...] = ()
    max_upload_bytes: int = 26_214_400
    max_image_pixels: int = 100_000_000
    max_prompt_chars: int = 50_000
    max_history_messages: int = 40
    max_history_chars: int = 150_000
    max_request_new_tokens: int = 4096
    max_concurrency: int = 1
    max_queue_depth: int = 8
    queue_timeout_seconds: float = 60.0
    web_dist_path: str = ""
    static_fallback_path: str = ""
    open_browser: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None, *, resolved_paths: Mapping[str, str]) -> "ServerSettings":
        data = dict(value or {})
        origins_raw = data.get("corsOrigins", [])
        if not isinstance(origins_raw, list) or any(not isinstance(item, str) for item in origins_raw):
            raise ConfigurationError("application.server.corsOrigins must be an array of strings.")
        origins = tuple(dict.fromkeys(item.strip() for item in origins_raw if item.strip()))

        def resolved(name: str, default_key: str) -> str:
            raw = str(data.get(name, "")).strip()
            if raw:
                return str(Path(raw).expanduser().resolve())
            return str(resolved_paths.get(default_key, ""))

        return cls(
            host=require_str(data.get("host", "127.0.0.1"), "application.server.host"),
            port=require_int(data.get("port", 8000), "application.server.port", minimum=1, maximum=65535),
            preload_model=require_bool(data.get("preloadModel", False), "application.server.preloadModel"),
            api_key_environment_variable=require_str(
                data.get("apiKeyEnvironmentVariable", "ARTIFEX_API_KEY"),
                "application.server.apiKeyEnvironmentVariable",
            ),
            allow_unauthenticated_remote=require_bool(
                data.get("allowUnauthenticatedRemote", False),
                "application.server.allowUnauthenticatedRemote",
            ),
            cors_origins=origins,
            max_upload_bytes=require_int(
                data.get("maxUploadBytes", 26_214_400),
                "application.server.maxUploadBytes",
                minimum=1,
                maximum=2_147_483_647,
            ),
            max_image_pixels=require_int(
                data.get("maxImagePixels", 100_000_000),
                "application.server.maxImagePixels",
                minimum=1,
                maximum=2_147_483_647,
            ),
            max_prompt_chars=require_int(
                data.get("maxPromptChars", 50_000),
                "application.server.maxPromptChars",
                minimum=1,
                maximum=10_000_000,
            ),
            max_history_messages=require_int(
                data.get("maxHistoryMessages", 40),
                "application.server.maxHistoryMessages",
                minimum=0,
                maximum=1000,
            ),
            max_history_chars=require_int(
                data.get("maxHistoryChars", 150_000),
                "application.server.maxHistoryChars",
                minimum=0,
                maximum=20_000_000,
            ),
            max_request_new_tokens=require_int(
                data.get("maxRequestNewTokens", 4096),
                "application.server.maxRequestNewTokens",
                minimum=32,
                maximum=131072,
            ),
            max_concurrency=require_int(
                data.get("maxConcurrency", 1),
                "application.server.maxConcurrency",
                minimum=1,
                maximum=64,
            ),
            max_queue_depth=require_int(
                data.get("maxQueueDepth", 8),
                "application.server.maxQueueDepth",
                minimum=0,
                maximum=10000,
            ),
            queue_timeout_seconds=require_float(
                data.get("queueTimeoutSeconds", 60.0),
                "application.server.queueTimeoutSeconds",
                minimum=0.1,
                maximum=86400.0,
            ),
            web_dist_path=resolved("webDistPath", "webDistDirectory"),
            static_fallback_path=resolved("staticFallbackPath", "webStaticDirectory"),
            open_browser=require_bool(data.get("openBrowser", False), "application.server.openBrowser"),
        )

    def with_overrides(self, **changes: Any) -> "ServerSettings":
        return replace(self, **{key: value for key, value in changes.items() if value is not None})

    @property
    def is_loopback(self) -> bool:
        return self.host.casefold() in {"127.0.0.1", "localhost", "::1"}
