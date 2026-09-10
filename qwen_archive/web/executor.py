"""Bounded async admission control for a single resident GPU model."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from ..errors import InferenceQueueFullError, InferenceQueueTimeoutError

T = TypeVar("T")


@dataclass(frozen=True)
class ExecutorSnapshot:
    active: int
    waiting: int
    max_concurrency: int
    max_queue_depth: int


class BoundedInferenceExecutor:
    """Runs blocking inference in worker threads with bounded admission.

    Waiting and active counters are updated under one lock and represented by
    independent flags so timeout/cancellation paths cannot decrement twice.
    """

    def __init__(
        self,
        *,
        max_concurrency: int,
        max_queue_depth: int,
        queue_timeout_seconds: float,
    ):
        self.max_concurrency = max(1, int(max_concurrency))
        self.max_queue_depth = max(0, int(max_queue_depth))
        self.queue_timeout_seconds = max(0.1, float(queue_timeout_seconds))
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._state_lock = asyncio.Lock()
        self._active = 0
        self._waiting = 0

    async def run(self, function: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        async with self._state_lock:
            if self._active >= self.max_concurrency and self._waiting >= self.max_queue_depth:
                raise InferenceQueueFullError("The local inference queue is full.")
            self._waiting += 1

        waiting_accounted = True
        active_accounted = False
        acquired = False
        try:
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=self.queue_timeout_seconds)
                acquired = True
            except TimeoutError as exc:
                raise InferenceQueueTimeoutError(
                    "Timed out waiting for local inference capacity."
                ) from exc

            async with self._state_lock:
                self._waiting -= 1
                waiting_accounted = False
                self._active += 1
                active_accounted = True
            return await asyncio.to_thread(function, *args, **kwargs)
        finally:
            async with self._state_lock:
                if active_accounted:
                    self._active = max(0, self._active - 1)
                elif waiting_accounted:
                    self._waiting = max(0, self._waiting - 1)
            if acquired:
                self._semaphore.release()

    async def snapshot(self) -> ExecutorSnapshot:
        async with self._state_lock:
            return ExecutorSnapshot(
                active=self._active,
                waiting=self._waiting,
                max_concurrency=self.max_concurrency,
                max_queue_depth=self.max_queue_depth,
            )
