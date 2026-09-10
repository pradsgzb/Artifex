"""Lossless Subject/XPSubject merge rules."""

from __future__ import annotations

from typing import Any, Mapping

from .constants import SCHEMA_VERSION
from .jsonutil import normalize_text

# These are the fields owned by a complete Prompts analysis. Unknown fields are
# retained so integrations can safely extend Subject JSON without Artifex data loss.
ANALYSIS_OWNED_FIELDS = frozenset(
    {
        "schemaVersion",
        "title",
        "description",
        "shade",
        "searchTerms",
        "categories",
        "folderClassification",
        "prompt",
    }
)


class SubjectMergeService:
    """Merges application-owned fields while preserving all foreign properties."""

    @staticmethod
    def _schema_version(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return SCHEMA_VERSION
        return max(parsed, SCHEMA_VERSION)

    @staticmethod
    def _base(existing: Mapping[str, Any] | None, raw_subject_text: str = "") -> dict[str, Any]:
        merged = dict(existing) if isinstance(existing, Mapping) else {}
        legacy = normalize_text(raw_subject_text)
        if not merged and legacy:
            merged["legacySubjectText"] = legacy
        merged.setdefault("schemaVersion", SCHEMA_VERSION)
        return merged

    def merge_analysis(
        self,
        existing: Mapping[str, Any] | None,
        analysis: Mapping[str, Any],
        *,
        raw_subject_text: str = "",
    ) -> dict[str, Any]:
        merged = self._base(existing, raw_subject_text)
        for key in ANALYSIS_OWNED_FIELDS:
            if key in analysis:
                merged[key] = analysis[key]
        merged["schemaVersion"] = self._schema_version(merged.get("schemaVersion"))
        return merged

    def merge_folder_classification(
        self,
        existing: Mapping[str, Any] | None,
        classification: Mapping[str, Any],
        *,
        raw_subject_text: str = "",
    ) -> dict[str, Any]:
        merged = self._base(existing, raw_subject_text)
        merged["folderClassification"] = dict(classification)
        merged["schemaVersion"] = self._schema_version(merged.get("schemaVersion"))
        return merged
