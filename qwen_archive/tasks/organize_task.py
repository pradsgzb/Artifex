"""Application service for metadata-first deterministic archive organization."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ..contracts import InferenceEngine, InferenceResultCache, MetadataRepository
from ..domain import FolderClassification, FolderValidationPolicy
from ..files import list_images, sha256_image_pixels
from ..metadata import MetadataSnapshot
from ..organizer import ClassifiedFile, FileOrganizer
from ..prompts import PromptLibrary
from ..subject import SubjectMergeService
from ..timestamps import capture_timestamps, restore_timestamps


class OrganizeTask:
    def __init__(
        self,
        *,
        input_path: Path,
        output_root: Path,
        recursive: bool,
        max_files: int,
        force: bool,
        preview: bool,
        hint: str,
        prefer_comments_prompt: bool,
        analyze_missing: bool,
        retries: int,
        retry_delay_seconds: float,
        metadata_attempts: int,
        folder_policy: FolderValidationPolicy,
        bucket_threshold: int,
        bucket_size: int,
        prompt_library: PromptLibrary,
        metadata: MetadataRepository,
        engine: InferenceEngine,
        cache: InferenceResultCache,
        subject_merger: SubjectMergeService,
        logger: logging.Logger,
    ):
        self.input_path = input_path
        self.output_root = output_root
        self.recursive = recursive
        self.max_files = max_files
        self.force = force
        self.preview = preview
        self.hint = hint
        self.prefer_comments_prompt = prefer_comments_prompt
        self.analyze_missing = analyze_missing
        self.retries = max(0, retries)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.metadata_attempts = max(1, metadata_attempts)
        self.folder_policy = folder_policy
        self.prompt_library = prompt_library
        self.metadata = metadata
        self.engine = engine
        self.cache = cache
        self.subject_merger = subject_merger
        self.logger = logger
        self.organizer = FileOrganizer(output_root, bucket_threshold, bucket_size)

    def run(self) -> dict[str, int]:
        exclusions = () if self.input_path.is_file() else (self.output_root,)
        files = list_images(
            self.input_path,
            self.recursive,
            self.max_files,
            excluded_roots=exclusions,
        )
        summary = {
            "discovered": len(files),
            "metadata": 0,
            "analyzed": 0,
            "cache": 0,
            "planned": 0,
            "moved": 0,
            "unchanged": 0,
            "unresolved": 0,
            "failed": 0,
        }
        self.logger.info(
            "Organize task: %d image(s) queued; output root: %s",
            len(files),
            self.output_root,
        )
        classified: list[ClassifiedFile] = []

        for index, image_path in enumerate(files, start=1):
            self.logger.info("[%d/%d] Classifying %s", index, len(files), image_path)
            stamps = capture_timestamps(image_path)
            try:
                snapshot = self.metadata.read(image_path)
                classification = None if self.force else self._classification_from_subject(snapshot.subject)
                if classification is not None:
                    summary["metadata"] += 1
                    self.logger.info("Reused valid Subject.folderClassification; Qwen was not invoked.")
                elif self.preview:
                    summary["unresolved"] += 1
                    self.logger.info("[PREVIEW] Missing valid folderClassification: %s", image_path)
                    continue
                elif self.analyze_missing:
                    classification, from_cache = self._infer_missing(image_path, snapshot)
                    summary["cache" if from_cache else "analyzed"] += 1
                    merged = self.subject_merger.merge_folder_classification(
                        snapshot.subject,
                        classification.to_dict(),
                        raw_subject_text=snapshot.raw_subject_text,
                    )
                    self.metadata.write_subject(
                        image_path,
                        merged,
                        attempts=self.metadata_attempts,
                    )
                    restore_timestamps(image_path, stamps)
                    self.logger.info("Persisted only the missing folderClassification field.")
                else:
                    summary["unresolved"] += 1
                    self._mark_failed(image_path, stamps)
                    continue

                self._log_folder_quality(classification, image_path)
                classified.append(
                    ClassifiedFile(image_path.resolve(), classification.folders, stamps)
                )
                self.logger.info("Resolved: %s", " > ".join(classification.folders))
            except Exception as exc:  # per-file isolation boundary
                summary["unresolved"] += 1
                self.logger.error("Classification failed for %s: %s", image_path, exc)
                if not self.preview:
                    self._mark_failed(image_path, stamps)

        if not classified:
            return summary

        rebalance = not self.input_path.is_file()
        plan = self.organizer.build_plan(classified, rebalance_existing=rebalance)
        summary["planned"] = len(plan)
        for entry in plan:
            try:
                status, destination = self.organizer.safe_move(entry, preview=self.preview)
                if status == "moved":
                    summary["moved"] += 1
                elif status == "unchanged":
                    summary["unchanged"] += 1
                self.logger.info("%s: %s -> %s", status.upper(), entry.source_path, destination)
            except Exception as exc:  # per-file move boundary
                summary["failed"] += 1
                self.logger.error("Move failed %s: %s", entry.source_path, exc)
                if entry.source_path.exists() and not self.preview:
                    self._mark_failed(
                        entry.source_path,
                        entry.stamps or capture_timestamps(entry.source_path),
                    )
        return summary

    def _classification_from_subject(self, subject: dict | None) -> FolderClassification | None:
        if not isinstance(subject, dict):
            return None
        try:
            return FolderClassification.from_dict(
                subject.get("folderClassification"),
                policy=self.folder_policy,
            )
        except Exception:
            return None

    def _infer_missing(
        self,
        image_path: Path,
        snapshot: MetadataSnapshot,
    ) -> tuple[FolderClassification, bool]:
        sources: list[tuple[str, str, Path | None]] = []
        if self.prefer_comments_prompt and snapshot.comments.strip():
            sources.append(("comments", snapshot.comments.strip(), None))
        sources.append(
            (
                "image",
                "Analyze this image and return only the required folderClassification JSON.",
                image_path,
            )
        )
        if not self.prefer_comments_prompt and snapshot.comments.strip():
            sources.append(("comments", snapshot.comments.strip(), None))

        errors: list[str] = []
        for mode, source_text, image_source in sources:
            try:
                system_prompt = self.prompt_library.folder_prompt(
                    hint=self.hint,
                    source_mode=mode,
                )
                user_text = (
                    "SOURCE IMAGE-GENERATION PROMPT / DESCRIPTION:\n" + source_text
                    if mode == "comments"
                    else source_text
                )
                source_hash = (
                    hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                    if mode == "comments"
                    else sha256_image_pixels(image_path)
                )
                request_hash = hashlib.sha256(
                    (system_prompt + "\0" + user_text).encode("utf-8")
                ).hexdigest()
                key = hashlib.sha256(
                    (
                        "organize-v2\0"
                        + self.engine.cache_identity
                        + "\0"
                        + mode
                        + "\0"
                        + source_hash
                        + "\0"
                        + request_hash
                    ).encode("utf-8")
                ).hexdigest()
                if not self.force:
                    cached = self.cache.get(key)
                    if cached is not None:
                        try:
                            result = FolderClassification.from_dict(
                                cached,
                                policy=self.folder_policy,
                            )
                            self.logger.info("Reused validated %s inference-cache result.", mode)
                            return result, True
                        except Exception as exc:
                            self.logger.warning("Discarding stale/invalid cached classification: %s", exc)

                self.logger.info(
                    "Classifying with %s inference.",
                    "Comments text-only" if mode == "comments" else "direct image",
                )

                def validate(value):
                    return FolderClassification.from_dict(value, policy=self.folder_policy)

                classification, _ = self.engine.generate_validated(
                    system_prompt=system_prompt,
                    user_text=user_text,
                    image_path=image_source,
                    validator=validate,
                    retries=self.retries,
                    retry_delay_seconds=self.retry_delay_seconds,
                )
                self.cache.put(
                    key=key,
                    task="organize",
                    model=self.engine.model_name,
                    source_hash=source_hash,
                    prompt_hash=request_hash,
                    response=classification.to_dict(),
                )
                return classification, False
            except Exception as exc:
                errors.append(f"{mode}: {exc}")
                self.logger.warning("%s classification source failed for %s: %s", mode, image_path.name, exc)
        raise RuntimeError("; ".join(errors) or "No classification source was available.")

    def _log_folder_quality(
        self,
        classification: FolderClassification,
        image_path: Path,
    ) -> None:
        self.logger.info(
            "Folder classification: %s (confidence=%.2f needsReview=%s)",
            " > ".join(classification.folders),
            classification.confidence,
            classification.needs_review,
        )
        for warning in classification.quality_warnings(policy=self.folder_policy):
            self.logger.warning(
                "Accepted folder classification quality warning for %s: %s",
                image_path.name,
                warning,
            )

    def _mark_failed(self, image_path: Path, stamps) -> None:
        try:
            added = self.metadata.append_tag(image_path, "MoveFailed", attempts=2)
            restore_timestamps(image_path, stamps)
            self.logger.warning(
                "%s MoveFailed tag for %s.",
                "Appended" if added else "Retained existing",
                image_path,
            )
        except Exception as exc:
            self.logger.error("Unable to append MoveFailed tag to %s: %s", image_path, exc)
