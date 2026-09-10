from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .constants import PROHIBITED_ARCHIVE_FOLDERS, SCHEMA_VERSION, SHADES
from .jsonutil import normalize_text
from .settings import require_bool

_WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _safe_folder_name(value: Any, *, fail_on_prohibited_folder: bool) -> str:
    text = normalize_text(value)
    if not text:
        raise ValueError("Folder classification contains an empty folder name.")
    if len(text) > 100:
        raise ValueError(f'Folder classification level is too long: "{text[:80]}...".')
    if _WINDOWS_INVALID.search(text):
        raise ValueError(f'Folder classification contains an unsafe Windows folder name: "{text}".')
    if text.endswith((" ", ".")):
        raise ValueError(f'Folder classification folder may not end with a space or period: "{text}".')
    if text.casefold() in _WINDOWS_RESERVED:
        raise ValueError(f'Folder classification uses reserved Windows name "{text}".')
    if fail_on_prohibited_folder and text.casefold() in PROHIBITED_ARCHIVE_FOLDERS:
        raise ValueError(f'Folder classification uses prohibited vague folder "{text}".')
    return text


@dataclass(frozen=True)
class FolderValidationPolicy:
    min_levels: int
    max_levels: int
    min_confidence: float
    fail_on_prohibited_folder: bool
    fail_on_low_confidence: bool
    fail_on_needs_review: bool

    def __post_init__(self) -> None:
        if self.min_levels < 1:
            raise ValueError("Folder classification minLevels must be >= 1.")
        if self.max_levels < self.min_levels:
            raise ValueError("Folder classification maxLevels must be >= minLevels.")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("Folder classification minConfidence must be between 0 and 1.")


@dataclass(frozen=True)
class FolderClassification:
    folders: tuple[str, ...]
    confidence: float
    subject: str
    needs_review: bool

    @classmethod
    def from_dict(cls, value: Any, *, policy: FolderValidationPolicy) -> "FolderClassification":
        if not isinstance(value, dict):
            raise ValueError("folderClassification must be a JSON object.")
        raw_folders = value.get("folders")
        if not isinstance(raw_folders, list) or not policy.min_levels <= len(raw_folders) <= policy.max_levels:
            raise ValueError(
                f"folderClassification.folders must contain exactly {policy.min_levels} to {policy.max_levels} levels."
            )
        folders = tuple(
            _safe_folder_name(item, fail_on_prohibited_folder=policy.fail_on_prohibited_folder)
            for item in raw_folders
        )
        folded = [item.casefold() for item in folders]
        if len(set(folded)) != len(folded):
            raise ValueError("folderClassification contains duplicate folder levels.")
        confidence = float(value.get("confidence"))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("folderClassification.confidence must be between 0 and 1.")
        subject = normalize_text(value.get("subject"))
        if not subject:
            raise ValueError("folderClassification.subject is required.")
        needs_review = require_bool(value.get("needsReview", False), "folderClassification.needsReview")
        if policy.fail_on_low_confidence and confidence < policy.min_confidence:
            raise ValueError(
                f"folderClassification confidence {confidence:.2f} is below required {policy.min_confidence:.2f}."
            )
        if policy.fail_on_needs_review and needs_review:
            raise ValueError("folderClassification is marked needsReview=true.")
        return cls(folders=folders, confidence=confidence, subject=subject, needs_review=needs_review)

    def quality_warnings(self, *, policy: FolderValidationPolicy) -> tuple[str, ...]:
        warnings: list[str] = []
        prohibited = [folder for folder in self.folders if folder.casefold() in PROHIBITED_ARCHIVE_FOLDERS]
        if prohibited:
            warnings.append("prohibited/vague folder suggestion(s): " + ", ".join(prohibited))
        if self.confidence < policy.min_confidence:
            warnings.append(
                f"confidence {self.confidence:.2f} is below preferred {policy.min_confidence:.2f}"
            )
        if self.needs_review:
            warnings.append("AI marked needsReview=true")
        return tuple(warnings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "folders": list(self.folders),
            "confidence": round(self.confidence, 4),
            "subject": self.subject,
            "needsReview": self.needs_review,
        }


@dataclass(frozen=True)
class PaintingAnalysis:
    schema_version: int
    title: str
    description: str
    shade: str
    search_terms: tuple[str, ...]
    categories: tuple[str, ...]
    folder_classification: FolderClassification
    prompt: str

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        allowed_categories: set[str],
        folder_policy: FolderValidationPolicy,
    ) -> "PaintingAnalysis":
        if not isinstance(value, dict):
            raise ValueError("Painting analysis must be a JSON object.")
        schema = int(value.get("schemaVersion", SCHEMA_VERSION))
        title = normalize_text(value.get("title"))
        description = normalize_text(value.get("description"))
        raw_shade = normalize_text(value.get("shade"))
        prompt = normalize_text(value.get("prompt"))
        if not title:
            raise ValueError("Painting analysis title is missing.")
        if len(title.split()) < 2:
            raise ValueError("Painting analysis title is too short.")
        if not description or len(description.split()) < 30:
            raise ValueError("Painting analysis description is incomplete.")
        if not raw_shade:
            raise ValueError("Painting analysis shade is missing.")
        shade_lookup = {item.casefold(): item for item in SHADES}
        shade = shade_lookup.get(raw_shade.casefold(), "Multi-Color")

        raw_terms = value.get("searchTerms")
        if not isinstance(raw_terms, list):
            raise ValueError("Painting analysis searchTerms must be an array.")
        terms: list[str] = []
        seen_terms: set[str] = set()
        for item in raw_terms:
            term = normalize_text(item).casefold()
            if not term or term in seen_terms:
                continue
            if len(term) > 48 or re.search(r"\s|[^\w]", term, flags=re.UNICODE):
                continue
            seen_terms.add(term)
            terms.append(term)
            if len(terms) == 50:
                break
        if not terms:
            raise ValueError("Painting analysis contains no valid one-word SEO tags.")

        raw_categories = value.get("categories")
        if not isinstance(raw_categories, list):
            raise ValueError("Painting analysis categories must be an array.")
        categories: list[str] = []
        for item in raw_categories:
            category = normalize_text(item)
            if category and category in allowed_categories and category not in categories:
                categories.append(category)
            if len(categories) == 3:
                break
        if not categories:
            raise ValueError("Painting analysis contains no valid marketplace category.")

        classification = FolderClassification.from_dict(
            value.get("folderClassification"), policy=folder_policy
        )
        if not prompt:
            raise ValueError("Painting analysis prompt is missing.")
        required_suffix = (
            "no digital image, no photo, no screenshot, no signatures, no text, no watermark, "
            "no frames, no borders, no UI, direct artwork only, not a photograph of a physical painting or drawing."
        )
        if required_suffix.casefold() not in prompt.casefold():
            prompt = f"{prompt.rstrip(' .')}, {required_suffix}"

        return cls(
            schema_version=max(schema, SCHEMA_VERSION),
            title=title,
            description=description,
            shade=shade,
            search_terms=tuple(terms),
            categories=tuple(categories),
            folder_classification=classification,
            prompt=prompt,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "title": self.title,
            "description": self.description,
            "shade": self.shade,
            "searchTerms": list(self.search_terms),
            "categories": list(self.categories),
            "folderClassification": self.folder_classification.to_dict(),
            "prompt": self.prompt,
        }
