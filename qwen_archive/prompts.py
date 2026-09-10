from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import SHADES
from .taxonomy import CategoryTaxonomy


@dataclass(frozen=True)
class PromptLibrary:
    analysis_template: str
    folder_template: str
    taxonomy_text: str

    @classmethod
    def load(
        cls,
        analysis_path: Path,
        folder_path: Path,
        taxonomy: CategoryTaxonomy | None = None,
        *,
        folder_min_levels: int,
        folder_max_levels: int,
    ) -> "PromptLibrary":
        def render(template: str) -> str:
            example = "[" + ", ".join(
                f'\"Level {index}\"' for index in range(1, folder_min_levels + 1)
            ) + "]"
            return (
                template
                .replace("{{FOLDER_MIN_LEVELS}}", str(folder_min_levels))
                .replace("{{FOLDER_MAX_LEVELS}}", str(folder_max_levels))
                .replace("{{FOLDER_ARRAY_EXAMPLE}}", example)
            )

        return cls(
            analysis_template=render(Path(analysis_path).read_text(encoding="utf-8").strip()),
            folder_template=render(Path(folder_path).read_text(encoding="utf-8").strip()),
            taxonomy_text=taxonomy.prompt_text if taxonomy is not None else "",
        )

    def analysis_prompt(self, *, hint: str = "", custom_prompt: str = "", source_mode: str = "image") -> str:
        blocks = [self.analysis_template]
        blocks.append("\nCANONICAL SHADE VALUES: " + " | ".join(SHADES))
        blocks.append("\nMARKETPLACE CATEGORY TAXONOMY — categories MUST use exact labels from this list:\n" + self.taxonomy_text)
        if hint.strip():
            blocks.append(
                "\nCONTEXT HINT:\n" + hint.strip() +
                "\nUse this only as contextual guidance; do not invent unsupported facts."
            )
        if custom_prompt.strip():
            blocks.append(
                "\nCUSTOM TRANSFORMATION-PROMPT GUIDANCE:\n" + custom_prompt.strip() +
                "\nApply this guidance only to the JSON prompt field."
            )
        if source_mode == "comments":
            blocks.append(
                "\nSOURCE MODE: The user content contains the original image-generation prompt/description. "
                "Use that text as the primary source of truth and infer conservatively."
            )
        else:
            blocks.append("\nSOURCE MODE: Analyze the supplied image directly.")
        return "\n".join(blocks).strip()

    def folder_prompt(self, *, hint: str = "", source_mode: str = "image") -> str:
        blocks = [self.folder_template]
        if hint.strip():
            blocks.append(
                "\nCONTEXT HINT:\n" + hint.strip() +
                "\nUse this only to disambiguate; do not invent unsupported facts."
            )
        blocks.append(
            "\nSOURCE MODE: " +
            ("Classify from the supplied image description/prompt text." if source_mode == "comments" else "Classify the supplied image directly.")
        )
        return "\n".join(blocks).strip()
