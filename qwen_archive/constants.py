from __future__ import annotations

from pathlib import Path

APP_NAME = "qwen-image-archive-cli"
VERSION = "2.0.0"
SCHEMA_VERSION = 4
SUPPORTED_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})

SHADES = (
    "Blue, Violet, Mauve",
    "Multi-Color",
    "Black and White",
    "Green",
    "Black, Dark Shades",
    "Red, Pink, Orange",
    "Yellow, Brown",
    "White, Light Shades",
)

PROHIBITED_ARCHIVE_FOLDERS = frozenset(
    value.casefold()
    for value in (
        "Other",
        "Miscellaneous",
        "General",
        "Images",
        "Image",
        "Pictures",
        "Picture",
        "Artwork",
        "Artworks",
        "Art",
        "Fine Art",
        "Painting",
        "Paintings",
        "Photograph",
        "Photographs",
        "Photography",
        "Drawing",
        "Drawings",
        "Digital Art",
        "Uncategorized",
        "Unknown",
    )
)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "config" / "settings.json"
DEFAULT_CATEGORIES_PATH = PACKAGE_ROOT / "config" / "categories.json"
DEFAULT_ANALYSIS_PROMPT_PATH = PACKAGE_ROOT / "config" / "prompts-analysis.txt"
DEFAULT_FOLDER_PROMPT_PATH = PACKAGE_ROOT / "config" / "folder-classification.txt"
