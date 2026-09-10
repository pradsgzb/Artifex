"""Image discovery and content hashing."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Iterable, Sequence

from .constants import SUPPORTED_EXTENSIONS


def is_supported_image(path: Path) -> bool:
    return path.suffix.casefold() in SUPPORTED_EXTENSIONS


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def list_images(
    input_path: Path,
    recursive: bool = False,
    max_files: int = 0,
    *,
    excluded_roots: Sequence[Path] = (),
) -> list[Path]:
    path = input_path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Input path does not exist: {path}")
    if path.is_file():
        if not is_supported_image(path):
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise ValueError(f"Unsupported image type: {path.suffix}. Supported: {supported}.")
        return [path]
    if not path.is_dir():
        raise ValueError(f"Input must be an image file or directory: {path}")

    exclusions = tuple(root.expanduser().resolve() for root in excluded_roots)
    iterator: Iterable[Path] = path.rglob("*") if recursive else path.iterdir()
    files: list[Path] = []
    for candidate in iterator:
        if not candidate.is_file() or not is_supported_image(candidate):
            continue
        resolved = candidate.resolve()
        if any(_is_below(resolved, excluded) for excluded in exclusions):
            continue
        files.append(resolved)
    files.sort(key=lambda item: (item.stat().st_mtime_ns, item.name.casefold(), os.path.normcase(str(item))))
    return files[:max_files] if max_files > 0 else files


def sha256_image_pixels(path: Path) -> str:
    """Hash display-oriented pixels without allocating a second full image buffer."""
    from PIL import Image, ImageOps

    digest = hashlib.sha256()
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        try:
            digest.update(f"RGB:{image.width}x{image.height}:".encode("ascii"))
            stripe_height = max(1, min(128, (1024 * 1024) // max(1, image.width * 3)))
            for top in range(0, image.height, stripe_height):
                bottom = min(image.height, top + stripe_height)
                stripe = image.crop((0, top, image.width, bottom))
                try:
                    digest.update(stripe.tobytes())
                finally:
                    stripe.close()
        finally:
            image.close()
    return "pixels-v1:" + digest.hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
