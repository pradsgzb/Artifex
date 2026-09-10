"""Application service for complete painting-analysis metadata generation."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from ..contracts import InferenceEngine, InferenceResultCache, MetadataRepository
from ..domain import FolderValidationPolicy, PaintingAnalysis
from ..files import list_images, sha256_image_pixels
from ..prompts import PromptLibrary
from ..subject import SubjectMergeService
from ..taxonomy import CategoryTaxonomy


class PromptsTask:
    def __init__(
        self,
        *,
        input_path: Path,
        recursive: bool,
        max_files: int,
        force: bool,
        preview: bool,
        hint: str,
        custom_prompt: str,
        prefer_comments_prompt: bool,
        default_capture_metadata: bool,
        batch_size: int,
        delay_ms: int,
        retries: int,
        retry_delay_seconds: float,
        metadata_attempts: int,
        folder_policy: FolderValidationPolicy,
        taxonomy: CategoryTaxonomy,
        prompt_library: PromptLibrary,
        metadata: MetadataRepository,
        engine: InferenceEngine,
        cache: InferenceResultCache,
        subject_merger: SubjectMergeService,
        logger: logging.Logger,
    ):
        self.input_path = input_path
        self.recursive = recursive
        self.max_files = max_files
        self.force = force
        self.preview = preview
        self.hint = hint
        self.custom_prompt = custom_prompt
        self.prefer_comments_prompt = prefer_comments_prompt
        self.default_capture_metadata = default_capture_metadata
        self.batch_size = max(1, batch_size)
        self.delay_ms = max(0, delay_ms)
        self.retries = max(0, retries)
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.metadata_attempts = max(1, metadata_attempts)
        self.folder_policy = folder_policy
        self.taxonomy = taxonomy
        self.prompt_library = prompt_library
        self.metadata = metadata
        self.engine = engine
        self.cache = cache
        self.subject_merger = subject_merger
        self.logger = logger

    def run(self) -> dict[str, int]:
        files = list_images(self.input_path, self.recursive, self.max_files)
        summary = {
            "discovered": len(files),
            "processed": 0,
            "analyzed": 0,
            "reused": 0,
            "cached": 0,
            "failed": 0,
        }
        self.logger.info("Prompts task: %d image(s) queued.", len(files))
        if self.taxonomy.duplicate_labels:
            self.logger.warning(
                "Marketplace taxonomy contains duplicate label(s); exact duplicates were ignored: %s",
                ", ".join(self.taxonomy.duplicate_labels),
            )
        if self.preview:
            for path in files:
                self.logger.info("[PREVIEW] %s", path)
            return summary

        for index, image_path in enumerate(files, start=1):
            summary["processed"] += 1
            self.logger.info("[%d/%d] %s", index, len(files), image_path)
            try:
                snapshot = self.metadata.read(image_path)
                existing = self._valid_existing(snapshot.subject)
                if existing is not None and not self.force:
                    summary["reused"] += 1
                    self.logger.info("Reusing valid Subject painting analysis: %s", existing.title)
                    continue

                if self.default_capture_metadata:
                    try:
                        self.metadata.write_default_capture_metadata(image_path, attempts=2)
                    except Exception as exc:  # one optional metadata group must not abort analysis
                        self.logger.warning(
                            "Default capture metadata could not be written for %s: %s",
                            image_path.name,
                            exc,
                        )

                use_comments = self.prefer_comments_prompt and bool(snapshot.comments.strip())
                source_mode = "comments" if use_comments else "image"
                system_prompt = self.prompt_library.analysis_prompt(
                    hint=self.hint,
                    custom_prompt=self.custom_prompt,
                    source_mode=source_mode,
                )
                if use_comments:
                    source_text = snapshot.comments.strip()
                    user_text = "SOURCE IMAGE-GENERATION PROMPT / DESCRIPTION:\n" + source_text
                    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                    image_source = None
                    self.logger.info("Using Comments/XPComment text-only inference.")
                else:
                    user_text = "Analyze this image and return the complete required painting-analysis JSON."
                    source_hash = sha256_image_pixels(image_path)
                    image_source = image_path
                    self.logger.info("Using direct image inference.")
                request_hash = hashlib.sha256(
                    (system_prompt + "\0" + user_text).encode("utf-8")
                ).hexdigest()
                key = hashlib.sha256(
                    (
                        "prompts-v2\0"
                        + self.engine.cache_identity
                        + "\0"
                        + source_hash
                        + "\0"
                        + request_hash
                    ).encode("utf-8")
                ).hexdigest()

                analysis: PaintingAnalysis | None = None
                if not self.force:
                    cached = self.cache.get(key)
                    if cached is not None:
                        try:
                            analysis = PaintingAnalysis.from_dict(
                                cached,
                                allowed_categories=self.taxonomy.allowed_set,
                                folder_policy=self.folder_policy,
                            )
                            summary["cached"] += 1
                            self.logger.info("Reused validated persistent inference-cache result.")
                        except Exception as exc:
                            self.logger.warning("Discarding stale/invalid cached result: %s", exc)
                if analysis is None:
                    def validate(value):
                        return PaintingAnalysis.from_dict(
                            value,
                            allowed_categories=self.taxonomy.allowed_set,
                            folder_policy=self.folder_policy,
                        )

                    analysis, _ = self.engine.generate_validated(
                        system_prompt=system_prompt,
                        user_text=user_text,
                        image_path=image_source,
                        validator=validate,
                        retries=self.retries,
                        retry_delay_seconds=self.retry_delay_seconds,
                    )
                    self.cache.put(
                        key=key,
                        task="prompts",
                        model=self.engine.model_name,
                        source_hash=source_hash,
                        prompt_hash=request_hash,
                        response=analysis.to_dict(),
                    )
                    summary["analyzed"] += 1

                self._log_folder_quality(analysis, image_path)
                merged_subject = self.subject_merger.merge_analysis(
                    snapshot.subject,
                    analysis.to_dict(),
                    raw_subject_text=snapshot.raw_subject_text,
                )
                self.metadata.write_analysis(
                    image_path,
                    merged_subject,
                    attempts=self.metadata_attempts,
                )
                self.logger.info(
                    "Metadata saved: title=%r shade=%s folders=%s",
                    analysis.title,
                    analysis.shade,
                    " > ".join(analysis.folder_classification.folders),
                )
            except Exception as exc:  # per-file isolation boundary
                summary["failed"] += 1
                self.logger.error("Failed %s: %s", image_path, exc)

            if self.delay_ms > 0 and index < len(files) and index % self.batch_size == 0:
                time.sleep(self.delay_ms / 1000.0)
        return summary

    def _log_folder_quality(self, analysis: PaintingAnalysis, image_path: Path) -> None:
        classification = analysis.folder_classification
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

    def _valid_existing(self, subject: dict | None) -> PaintingAnalysis | None:
        if not isinstance(subject, dict):
            return None
        try:
            return PaintingAnalysis.from_dict(
                subject,
                allowed_categories=self.taxonomy.allowed_set,
                folder_policy=self.folder_policy,
            )
        except Exception:
            return None
