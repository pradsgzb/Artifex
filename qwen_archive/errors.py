"""Typed application exceptions used across CLI, batch, and HTTP boundaries."""

from __future__ import annotations


class ArtifexError(RuntimeError):
    """Base class for expected Artifex operational failures."""


class ConfigurationError(ArtifexError, ValueError):
    """Raised when settings are missing, malformed, or internally inconsistent."""


class InferenceError(ArtifexError):
    """Base class for local-model inference failures."""


class InferenceTimeoutError(InferenceError, TimeoutError):
    """Raised when a bounded generation exceeds its configured wall-clock limit."""


class IncompleteJsonError(InferenceError, ValueError):
    """Raised when model output starts JSON but does not close the top-level value."""


class InvalidModelResponseError(InferenceError, ValueError):
    """Raised when model output cannot be parsed or fails domain validation."""


class InferenceQueueFullError(InferenceError):
    """Raised when the embedded service has no safe capacity for another request."""


class InferenceQueueTimeoutError(InferenceError, TimeoutError):
    """Raised when a queued request cannot enter inference within the configured limit."""


class RequestValidationError(ArtifexError, ValueError):
    """Raised when an HTTP inference request is malformed or semantically invalid."""


class PayloadTooLargeError(RequestValidationError):
    """Raised when an HTTP payload exceeds an explicit configured size limit."""
