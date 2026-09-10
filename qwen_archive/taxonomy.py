"""Marketplace taxonomy loader with deterministic diagnostics and deduplication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator


class CategoryTaxonomy:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("categories"), list):
            raise ValueError(f"Invalid categories JSON: {self.path}")
        self.root_folder = str(payload.get("rootFolder") or "Categories").strip() or "Categories"
        self.tree = payload["categories"]
        labels = tuple(self._walk_labels(self.tree))
        unique: list[str] = []
        duplicates: list[str] = []
        seen: set[str] = set()
        for label in labels:
            folded = label.casefold()
            if folded in seen:
                duplicates.append(label)
                continue
            seen.add(folded)
            unique.append(label)
        self.allowed_categories = tuple(unique)
        self.allowed_set = set(self.allowed_categories)
        self.duplicate_labels = tuple(duplicates)
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.prompt_text = " | ".join(self.allowed_categories)

    def _walk_labels(self, nodes: list[dict[str, Any]]) -> Iterator[str]:
        for node in nodes:
            if not isinstance(node, dict):
                raise ValueError(f"Invalid category node in {self.path}: expected object.")
            label = str(node.get("label") or "").strip()
            if label:
                yield label
            children = node.get("children")
            if isinstance(children, list):
                yield from self._walk_labels(children)
            elif children is not None:
                raise ValueError(f"Invalid children value for category {label!r} in {self.path}.")

    def render_tree(self) -> str:
        return self._render_prompt(self.tree)

    def _render_prompt(self, nodes: list[dict[str, Any]], depth: int = 0) -> str:
        lines: list[str] = []
        for node in nodes:
            label = str(node.get("label") or "").strip()
            if not label:
                continue
            lines.append(f"{'  ' * depth}- {label}")
            children = node.get("children")
            if isinstance(children, list):
                child_text = self._render_prompt(children, depth + 1)
                if child_text:
                    lines.append(child_text)
        return "\n".join(lines)
