from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .jsonutil import build_json_candidates, compact_json, normalize_text
from .timestamps import (
    FileTimestamps,
    best_available_creation_datetime,
    capture_timestamps,
    restore_timestamps,
)


@dataclass
class MetadataSnapshot:
    raw: dict[str, Any]
    subject: dict[str, Any] | None
    subject_source: str | None
    raw_subject_text: str
    comments: str
    title: str
    tags: list[str]


class ExifToolError(RuntimeError):
    pass


class ExifToolService:
    def __init__(
        self,
        executable: str = "exiftool",
        *,
        timeout_seconds: int = 45,
        capture_metadata: Mapping[str, Any] | None = None,
        logger=None,
    ):
        self.executable = self._resolve_executable(executable)
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.capture_metadata = {str(key): value for key, value in dict(capture_metadata or {}).items()}
        self.logger = logger

    @staticmethod
    def _resolve_executable(executable: str) -> str:
        candidate = str(executable or "exiftool").strip()
        if Path(candidate).expanduser().exists():
            return str(Path(candidate).expanduser().resolve())
        found = shutil.which(candidate)
        if found:
            return found
        if os.name == "nt" and not candidate.lower().endswith(".exe"):
            found = shutil.which(candidate + ".exe")
            if found:
                return found
        raise FileNotFoundError(
            f"ExifTool executable was not found: {candidate}. Install ExifTool or pass --exiftool-path."
        )

    def _run(self, args: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
        command = [self.executable, *args]
        result = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout or self.timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "ExifTool failed").strip()
            raise ExifToolError(f"ExifTool exited with {result.returncode}: {message}")
        return result

    def _read_selected_tags(self, file_path: Path, keys: Iterable[str]) -> dict[str, Any]:
        path = Path(file_path).resolve()
        selected = [normalize_text(key).lstrip("-") for key in keys if normalize_text(key)]
        if not selected:
            return {}
        result = self._run(["-j", *[f"-{key}" for key in selected], str(path)])
        try:
            payload = json.loads(result.stdout)
            return payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else {}
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise ExifToolError(f"ExifTool returned invalid JSON for {path}: {exc}") from exc

    def read(self, file_path: Path) -> MetadataSnapshot:
        path = Path(file_path).resolve()
        result = self._run([
            "-j", "-charset", "filename=UTF8",
            "-XPSubject", "-Subject", "-XPComment", "-UserComment", "-ImageDescription",
            "-Title", "-XPKeywords", "-Keywords",
            str(path),
        ])
        try:
            payload = json.loads(result.stdout)
            tags = payload[0] if isinstance(payload, list) and payload else {}
        except (json.JSONDecodeError, IndexError, TypeError) as exc:
            raise ExifToolError(f"ExifTool returned invalid JSON for {path}: {exc}") from exc

        subject, source, raw_subject = self._parse_subject(tags)
        comments = self._first_text(tags, ["XPComment", "UserComment", "ImageDescription"])
        title = self._first_text(tags, ["Title"])
        tag_values: list[str] = []
        for key in ("XPKeywords", "Keywords"):
            tag_values.extend(self._split_tags(tags.get(key)))
        tags_normalized: list[str] = []
        seen: set[str] = set()
        for value in tag_values:
            folded = value.casefold()
            if not value or folded in seen:
                continue
            seen.add(folded)
            tags_normalized.append(value)
        return MetadataSnapshot(tags, subject, source, raw_subject, comments, title, tags_normalized)

    def _parse_subject(self, tags: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None, str]:
        raw_text = ""
        for key in ("XPSubject", "Subject"):
            value = tags.get(key)
            if not raw_text:
                raw_text = normalize_text(value)
            for candidate in build_json_candidates(value):
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed, key, raw_text
        return None, None, raw_text

    @staticmethod
    def _first_text(tags: dict[str, Any], keys: Iterable[str]) -> str:
        for key in keys:
            value = tags.get(key)
            if isinstance(value, list):
                value = " ".join(normalize_text(item) for item in value if normalize_text(item))
            text = normalize_text(value)
            if text:
                return text
        return ""

    @staticmethod
    def _split_tags(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            result: list[str] = []
            for item in value:
                result.extend(ExifToolService._split_tags(item))
            return result
        text = normalize_text(value)
        if not text:
            return []
        # XPKeywords commonly uses semicolons; generic Keywords may be list-valued.
        separators = ";" if ";" in text else ","
        return [part.strip() for part in text.split(separators) if part.strip()]

    def _write_args_file(self, file_path: Path, assignments: list[str], *, preserve_mtime: bool = True) -> None:
        path = Path(file_path).resolve()
        stamps = capture_timestamps(path)
        fd, arg_name = tempfile.mkstemp(prefix="qwen-exiftool-", suffix=".args", text=True)
        os.close(fd)
        arg_path = Path(arg_name)
        try:
            lines = ["-overwrite_original_in_place"]
            if preserve_mtime:
                lines.append("-P")
            if path.suffix.casefold() in {".jpg", ".jpeg"}:
                lines.extend(["-CodedCharacterSet=UTF8", "-IPTCDigest=new"])
            lines.extend(assignments)
            lines.append(str(path))
            arg_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            # ExifTool requires filename charset to appear before -@ for Unicode
            # paths contained inside a UTF-8 argument file on Windows.
            self._run(["-charset", "filename=UTF8", "-@", str(arg_path)])
        finally:
            arg_path.unlink(missing_ok=True)
            # Restore timestamps even when ExifTool itself fails after touching the file.
            if path.exists():
                restore_timestamps(path, stamps)

    def write_subject(self, file_path: Path, subject: dict[str, Any], *, attempts: int = 3) -> None:
        expected = compact_json(subject)
        self._write_verified(
            file_path,
            [f"-XPSubject={expected}"],
            lambda snapshot: snapshot.subject == subject,
            attempts=attempts,
            label="Subject/XPSubject JSON",
        )

    def write_analysis(self, file_path: Path, analysis: dict[str, Any], *, attempts: int = 3) -> None:
        subject_json = compact_json(analysis)
        title = normalize_text(analysis.get("title"))
        prompt = " ".join(normalize_text(analysis.get("prompt")).split())
        search_terms = [normalize_text(item) for item in analysis.get("searchTerms", []) if normalize_text(item)]
        if not title or not prompt or not search_terms:
            raise ValueError("Analysis metadata requires title, prompt, and searchTerms.")

        before = self.read(file_path)
        merged_tags: list[str] = []
        seen_tags: set[str] = set()
        for tag in [*before.tags, *search_terms]:
            clean = normalize_text(tag)
            folded = clean.casefold()
            if not clean or folded in seen_tags:
                continue
            seen_tags.add(folded)
            merged_tags.append(clean)

        assignments = [
            f"-XPSubject={subject_json}",
            f"-Title={title}",
            f"-XPKeywords={'; '.join(merged_tags)}",
            "-Keywords=",
        ]
        assignments.extend(f"-Keywords+={tag}" for tag in merged_tags)
        assignments.extend([
            f"-XPComment={prompt}",
            f"-UserComment={prompt}",
            f"-ImageDescription={prompt}",
        ])

        def verify(snapshot: MetadataSnapshot) -> bool:
            actual = {tag.casefold() for tag in snapshot.tags}
            return (
                snapshot.subject == analysis
                and snapshot.title == title
                and normalize_text(snapshot.comments) == prompt
                and all(tag.casefold() in actual for tag in merged_tags)
            )

        self._write_verified(file_path, assignments, verify, attempts=attempts, label="complete painting metadata")

    def append_tag(self, file_path: Path, tag: str, *, attempts: int = 2) -> bool:
        clean = normalize_text(tag)
        if not clean:
            return False
        snapshot = self.read(file_path)
        if any(existing.casefold() == clean.casefold() for existing in snapshot.tags):
            return False
        merged = [*snapshot.tags, clean]
        assignments = [f"-XPKeywords={'; '.join(merged)}", "-Keywords="]
        assignments.extend(f"-Keywords+={item}" for item in merged)
        self._write_verified(
            file_path,
            assignments,
            lambda after: any(item.casefold() == clean.casefold() for item in after.tags),
            attempts=attempts,
            label=f'tag "{clean}"',
        )
        return True

    def write_default_capture_metadata(self, file_path: Path, *, attempts: int = 2) -> None:
        path = Path(file_path).resolve()
        if not self.capture_metadata:
            if self.logger is not None:
                self.logger.debug("Default capture metadata is enabled but no values are configured.")
            return

        created = best_available_creation_datetime(path).strftime("%Y:%m:%d %H:%M:%S")
        defaults: dict[str, str] = {}
        for key, value in self.capture_metadata.items():
            rendered = normalize_text(value).replace("${FILE_CREATION_TIME}", created)
            if rendered:
                defaults[key] = rendered
        if not defaults:
            return
        before = self._read_selected_tags(path, defaults.keys())
        missing = {key: value for key, value in defaults.items() if not normalize_text(before.get(key))}
        if not missing:
            return

        # Tag formats are infrastructure rules; all deployment-specific values
        # come from settings.json. Numeric EXIF tags use # to avoid locale/label
        # ambiguity across ExifTool versions.
        numeric_tags = {
            "FocalLength",
            "FNumber",
            "ISO",
            "ExposureProgram",
            "MeteringMode",
            "WhiteBalance",
            "Flash",
        }
        assignments: list[str] = []
        for key, value in missing.items():
            suffix = "#" if key in numeric_tags else ""
            assignments.append(f"-EXIF:{key}{suffix}={value}")
        verification_error = {"message": ""}

        def verify(_: MetadataSnapshot) -> bool:
            data = self._read_selected_tags(path, missing.keys())
            still_missing = [key for key in missing if not normalize_text(data.get(key))]
            verification_error["message"] = ", ".join(still_missing)
            return not still_missing

        try:
            self._write_verified(
                path,
                assignments,
                verify,
                attempts=attempts,
                label="missing default capture metadata",
            )
        except ExifToolError as exc:
            detail = verification_error["message"]
            if detail and "read-back mismatch" in str(exc):
                raise ExifToolError(f"{exc}; tags still missing after write: {detail}") from exc
            raise

    def _write_verified(self, file_path: Path, assignments: list[str], verifier, *, attempts: int, label: str) -> None:
        errors: list[str] = []
        total = max(1, int(attempts))
        for attempt in range(1, total + 1):
            try:
                self._write_args_file(file_path, assignments)
                snapshot = self.read(file_path)
                if verifier(snapshot):
                    return
                errors.append(f"attempt {attempt}: read-back mismatch")
            except Exception as exc:  # noqa: BLE001 - bounded retry boundary
                errors.append(f"attempt {attempt}: {exc}")
            if attempt < total:
                time.sleep(0.25 * attempt)
        raise ExifToolError(f"Failed to persist {label} after {total} attempt(s): {' | '.join(errors)}")
