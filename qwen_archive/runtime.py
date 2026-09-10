"""Composition helpers for the shared local-Qwen runtime."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Mapping

from .engine import QwenEngine
from .settings import AdaptiveGenerationPolicy, require_bool


@dataclass(frozen=True)
class EngineSettings:
    model_name: str
    model_cache_dir: str
    local_files_only: bool
    device: str
    precision: str
    attention: str
    quantization: str
    llm_int8_threshold: float
    compile_model: bool
    max_new_tokens: int
    max_inference_seconds: float
    max_image_side: int
    temperature: float
    top_p: float
    top_k: int
    sampling: bool
    seed: int
    adaptive_policy: AdaptiveGenerationPolicy

    @classmethod
    def from_application(cls, app: Mapping[str, Any]) -> "EngineSettings":
        max_tokens = int(app.get("maxNewTokens", 1024))
        max_side = int(app.get("maxImageSide", 1024))
        adaptive = app.get("adaptiveGeneration", {})
        return cls(
            model_name=str(app.get("model", "Qwen/Qwen3.5-4B")),
            model_cache_dir=str(app.get("modelCacheDir", "")),
            local_files_only=require_bool(app.get("localFilesOnly", False), "application.localFilesOnly"),
            device=str(app.get("device", "auto")),
            precision=str(app.get("precision", "auto")),
            attention=str(app.get("attention", "sdpa")),
            quantization=str(app.get("quantization", "none")),
            llm_int8_threshold=float(app.get("llmInt8Threshold", 6.0)),
            compile_model=require_bool(app.get("compileModel", False), "application.compileModel"),
            max_new_tokens=max_tokens,
            max_inference_seconds=float(app.get("maxInferenceSeconds", 180.0)),
            max_image_side=max_side,
            temperature=float(app.get("temperature", 0.2)),
            top_p=float(app.get("topP", 0.8)),
            top_k=int(app.get("topK", 20)),
            sampling=require_bool(app.get("sampling", False), "application.sampling"),
            seed=int(app.get("seed", 42)),
            adaptive_policy=AdaptiveGenerationPolicy.from_mapping(
                adaptive if isinstance(adaptive, Mapping) else {},
                initial_max_new_tokens=max_tokens,
                initial_max_image_side=max_side,
            ),
        )

    def with_overrides(self, **values: Any) -> "EngineSettings":
        permitted = set(self.__dataclass_fields__)
        changes = {key: value for key, value in values.items() if key in permitted and value is not None}
        settings = replace(self, **changes)
        if "adaptive_policy" not in changes:
            settings = replace(
                settings,
                adaptive_policy=replace(
                    settings.adaptive_policy,
                    max_new_tokens_ceiling=max(
                        settings.max_new_tokens,
                        settings.adaptive_policy.max_new_tokens_ceiling,
                    ),
                    minimum_image_side=min(
                        settings.adaptive_policy.minimum_image_side,
                        settings.max_image_side or settings.adaptive_policy.minimum_image_side,
                    ),
                ),
            )
        return settings

    def build(self, logger: logging.Logger) -> QwenEngine:
        return QwenEngine(
            model_name=self.model_name,
            model_cache_dir=self.model_cache_dir,
            local_files_only=self.local_files_only,
            device=self.device,
            precision=self.precision,
            attention=self.attention,
            quantization=self.quantization,
            llm_int8_threshold=self.llm_int8_threshold,
            compile_model=self.compile_model,
            max_new_tokens=self.max_new_tokens,
            max_inference_seconds=self.max_inference_seconds,
            max_image_side=self.max_image_side,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            sampling=self.sampling,
            seed=self.seed,
            adaptive_policy=self.adaptive_policy,
            logger=logger,
        )
