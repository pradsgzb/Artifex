from __future__ import annotations

import errno
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .files import is_supported_image, sha256_file
from .timestamps import FileTimestamps, capture_timestamps, restore_timestamps


@dataclass
class ClassifiedFile:
    source_path: Path
    folders: tuple[str, ...]
    stamps: FileTimestamps


@dataclass
class MovePlanEntry:
    source_path: Path
    destination_path: Path
    folders: tuple[str, ...]
    stamps: FileTimestamps | None
    classified_input: bool
    reason: str


class FileOrganizer:
    def __init__(self, output_root: Path, bucket_threshold: int = 10, bucket_size: int = 250):
        self.output_root = Path(output_root).resolve()
        self.bucket_threshold = max(0, int(bucket_threshold))
        self.bucket_size = max(1, int(bucket_size))

    @staticmethod
    def same_path(left: Path, right: Path) -> bool:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))

    @staticmethod
    def is_bucket_name(name: str) -> bool:
        import re
        return re.fullmatch(r"\d{5,} - \d{5,}", name or "") is not None

    def bucket_name(self, index: int) -> str:
        zero_based = max(0, int(index))
        start = (zero_based // self.bucket_size) * self.bucket_size + 1
        end = start + self.bucket_size - 1
        width = max(5, len(str(end)))
        return f"{start:0{width}d} - {end:0{width}d}"

    def inventory_leaf(self, leaf: Path) -> list[Path]:
        if not leaf.exists() or not leaf.is_dir():
            return []
        result: list[Path] = []
        for entry in leaf.iterdir():
            if entry.is_file() and is_supported_image(entry):
                result.append(entry.resolve())
            elif entry.is_dir() and self.is_bucket_name(entry.name):
                result.extend(child.resolve() for child in entry.iterdir() if child.is_file() and is_supported_image(child))
        return result

    @staticmethod
    def _stable_key(path: Path):
        stat = path.stat()
        birth = getattr(stat, "st_birthtime_ns", None)
        # POSIX ctime is metadata-change time, not creation time. Modification
        # time is the stable fallback when birth time is unavailable.
        order_time = birth if birth is not None else stat.st_mtime_ns
        return (order_time, stat.st_mtime_ns, path.name.casefold(), str(path).casefold())

    def _collision_path(self, source: Path, desired: Path, reserved: set[str]) -> Path:
        if self.same_path(source, desired):
            return desired

        def available(candidate: Path) -> bool:
            key = os.path.normcase(str(candidate.resolve(strict=False)))
            return key not in reserved and not candidate.exists()

        if available(desired):
            return desired
        for counter in range(2, 100000):
            candidate = desired.with_name(f"{desired.stem}__{counter}{desired.suffix}")
            if available(candidate):
                return candidate
        raise RuntimeError(f"Unable to find collision-safe destination for {source}")

    def build_plan(self, items: list[ClassifiedFile], *, rebalance_existing: bool) -> list[MovePlanEntry]:
        groups: dict[tuple[str, ...], list[ClassifiedFile]] = {}
        destination_by_source: dict[str, Path] = {}
        classified_by_source: dict[str, ClassifiedFile] = {}
        for item in items:
            groups.setdefault(item.folders, []).append(item)
            key = os.path.normcase(str(item.source_path.resolve()))
            destination_by_source[key] = self.output_root.joinpath(*item.folders).resolve()
            classified_by_source[key] = item

        reserved: set[str] = set()
        result: list[MovePlanEntry] = []
        for folders, group_items in groups.items():
            leaf = self.output_root.joinpath(*folders)
            resident = []
            for path in self.inventory_leaf(leaf):
                key = os.path.normcase(str(path.resolve()))
                other_destination = destination_by_source.get(key)
                if other_destination is None or self.same_path(other_destination, leaf):
                    resident.append(path)

            combined: dict[str, Path] = {os.path.normcase(str(p.resolve())): p for p in resident}
            for item in group_items:
                combined[os.path.normcase(str(item.source_path.resolve()))] = item.source_path.resolve()
            all_files = list(combined.values())
            existing_buckets = any(self.is_bucket_name(p.parent.name) for p in resident)
            bucket_mode = existing_buckets or len(all_files) > self.bucket_threshold

            if rebalance_existing:
                ordered = sorted(all_files, key=self._stable_key)
                candidates = [(path, index) for index, path in enumerate(ordered)]
            else:
                # Single-file mode never moves unrelated residents.
                candidates = []
                for item in group_items:
                    index = len(resident) + len(candidates)
                    candidates.append((item.source_path.resolve(), index))

            for source, index in candidates:
                classified = classified_by_source.get(os.path.normcase(str(source.resolve())))
                if bucket_mode:
                    target_dir = leaf / self.bucket_name(index)
                else:
                    target_dir = leaf
                desired = target_dir / source.name
                destination = self._collision_path(source, desired, reserved)
                reserved.add(os.path.normcase(str(destination.resolve(strict=False))))
                result.append(
                    MovePlanEntry(
                        source_path=source,
                        destination_path=destination,
                        folders=folders,
                        stamps=classified.stamps if classified else capture_timestamps(source),
                        classified_input=classified is not None,
                        reason="category reconciliation" if classified else "bucket rebalance",
                    )
                )

        # Deduplicate source paths defensively.
        deduped: list[MovePlanEntry] = []
        seen: set[str] = set()
        for entry in result:
            key = os.path.normcase(str(entry.source_path.resolve()))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(entry)
        return deduped

    def safe_move(self, entry: MovePlanEntry, *, preview: bool = False) -> tuple[str, Path]:
        source = entry.source_path.resolve()
        destination = entry.destination_path.resolve(strict=False)
        if self.same_path(source, destination):
            if not preview and entry.stamps:
                restore_timestamps(source, entry.stamps)
            return "unchanged", source
        if preview:
            return "planned", destination
        if not source.exists():
            raise FileNotFoundError(f"Source file no longer exists: {source}")
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite existing file: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        stamps = entry.stamps or capture_timestamps(source)

        try:
            os.rename(source, destination)
            restore_timestamps(destination, stamps)
            return "moved", destination
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise

        # Transactional cross-volume fallback.
        temp = destination.with_name(
            f".{destination.name}.qwen-move-{os.getpid()}-{uuid.uuid4().hex}.tmp"
        )
        try:
            source_hash = sha256_file(source)
            with source.open("rb") as src, temp.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            restore_timestamps(temp, stamps)
            if sha256_file(temp) != source_hash:
                raise IOError(f"Cross-volume copy hash verification failed for {source}")
            os.rename(temp, destination)
            restore_timestamps(destination, stamps)
            source.unlink()
            return "moved", destination
        except Exception:
            temp.unlink(missing_ok=True)
            raise
