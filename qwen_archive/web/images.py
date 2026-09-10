"""Bounded image decoding, format verification, and temporary-file lifecycle."""

from __future__ import annotations

import base64
import binascii
import contextlib
import tempfile
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterator

from fastapi import UploadFile
from PIL import Image

from ..errors import PayloadTooLargeError, RequestValidationError

_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


@dataclass(frozen=True)
class ValidatedImage:
    path: Path
    mime_type: str
    width: int
    height: int


def _validate_image_bytes(
    data: bytes,
    *,
    declared_mime_type: str | None,
    max_image_pixels: int,
) -> tuple[str, str, int, int]:
    if not data:
        raise RequestValidationError("The uploaded image is empty.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                actual_format = str(image.format or "").upper()
                width, height = image.size
                image.verify()
    except Exception as exc:
        raise RequestValidationError(f"Uploaded content is not a valid supported image: {exc}") from exc
    if actual_format not in _FORMATS:
        raise RequestValidationError(
            f"Unsupported image format {actual_format or 'unknown'}; use JPEG, PNG, or WebP."
        )
    mime_type, suffix = _FORMATS[actual_format]
    if width < 1 or height < 1 or width * height > max_image_pixels:
        raise RequestValidationError(
            f"Image dimensions {width}x{height} exceed the configured pixel limit."
        )
    declared = (declared_mime_type or "").split(";", 1)[0].strip().casefold()
    if declared and declared not in {mime_type, "image/jpg" if mime_type == "image/jpeg" else mime_type}:
        raise RequestValidationError(
            f"Declared MIME type {declared_mime_type!r} does not match actual {mime_type}."
        )
    return mime_type, suffix, width, height


@contextlib.contextmanager
def temporary_validated_image(
    data: bytes,
    *,
    declared_mime_type: str | None,
    max_image_pixels: int,
) -> Iterator[ValidatedImage]:
    mime_type, suffix, width, height = _validate_image_bytes(
        data,
        declared_mime_type=declared_mime_type,
        max_image_pixels=max_image_pixels,
    )
    handle = tempfile.NamedTemporaryFile(prefix="artifex-api-", suffix=suffix, delete=False)
    path = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
        yield ValidatedImage(path=path, mime_type=mime_type, width=width, height=height)
    finally:
        path.unlink(missing_ok=True)


def decode_base64_image(value: str, *, max_bytes: int) -> bytes:
    text = value.strip()
    if text.startswith("data:"):
        try:
            _, text = text.split(",", 1)
        except ValueError as exc:
            raise RequestValidationError("Malformed data URL for imageBase64.") from exc
    # Base64 expands input by roughly 4/3; reject obviously excessive payloads
    # before allocating the decoded byte array.
    if len(text) > ((max_bytes + 2) // 3) * 4 + 16:
        raise PayloadTooLargeError("Base64 image exceeds the configured upload limit.")
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RequestValidationError("imageBase64 is not valid Base64.") from exc
    if len(data) > max_bytes:
        raise PayloadTooLargeError("Decoded image exceeds the configured upload limit.")
    return data


async def read_upload_limited(upload: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, max_bytes + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > max_bytes:
            raise PayloadTooLargeError("Uploaded image exceeds the configured upload limit.")
    return b"".join(chunks)
