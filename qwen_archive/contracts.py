"""Dependency-inversion contracts for core Artifex services.

The concrete local-Qwen, ExifTool, SQLite, and filesystem implementations are
composed at the application boundary. Batch use cases depend on these protocols
instead of constructing infrastructure directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class InferenceEngine(Protocol):
    model_name: str

    @property
    def cache_identity(self) -> str:
        ...

    @property
    def loaded(self) -> bool:
        ...

    def load(self) -> None:
        ...

    def generate(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_path: Path | None = None,
        history: Sequence[Mapping[str, str]] = (),
        max_new_tokens: int | None = None,
        max_image_side: int | None = None,
        response_format: str = "text",
    ) -> str:
        ...

    def generate_validated(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_path: Path | None,
        validator: Callable[[Any], T],
        retries: int,
        retry_delay_seconds: float,
    ) -> tuple[T, str]:
        ...


@runtime_checkable
class InferenceResultCache(Protocol):
    enabled: bool

    def get(self, key: str) -> Any | None:
        ...

    def put(
        self,
        *,
        key: str,
        task: str,
        model: str,
        source_hash: str,
        prompt_hash: str,
        response: Any,
    ) -> None:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class MetadataRepository(Protocol):
    def read(self, file_path: Path):
        ...

    def write_subject(self, file_path: Path, subject: dict[str, Any], *, attempts: int = 3) -> None:
        ...

    def write_analysis(self, file_path: Path, analysis: dict[str, Any], *, attempts: int = 3) -> None:
        ...

    def append_tag(self, file_path: Path, tag: str, *, attempts: int = 2) -> bool:
        ...

    def write_default_capture_metadata(self, file_path: Path, *, attempts: int = 2) -> None:
        ...


@runtime_checkable
class BatchTask(Protocol):
    def run(self) -> dict[str, int]:
        ...


class TaskFactory(Protocol):
    def create(self, task_name: str, *, logger: logging.Logger) -> BatchTask:
        ...
