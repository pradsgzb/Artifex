#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
OUTPUT="${1:-$ROOT/Artifex-v2.0.0-source.zip}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/Artifex"

python3 - "$ROOT" "$STAGE/Artifex" <<'PY'
from __future__ import annotations
import shutil
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
excluded_names = {
    '.git', '.idea', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.venv',
    '__pycache__', 'node_modules', 'runtime', 'models', 'cache', 'logs',
    'deployment', 'tools/exiftool', 'dist'
}
excluded_suffixes = {'.pyc', '.pyo', '.log', '.zip'}

for path in sorted(source.rglob('*')):
    relative = path.relative_to(source)
    parts = set(relative.parts)
    if parts & excluded_names:
        continue
    if relative.parts[:2] == ('tools', 'exiftool'):
        continue
    if path.suffix.casefold() in excluded_suffixes:
        continue
    if path.name in {'.DS_Store', 'Thumbs.db'}:
        continue
    target = destination / relative
    if path.is_dir():
        target.mkdir(parents=True, exist_ok=True)
    elif path.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
PY

mkdir -p "$(dirname -- "$OUTPUT")"
rm -f "$OUTPUT"
(
  cd "$STAGE"
  python3 - "$OUTPUT" <<'PY'
from pathlib import Path
import sys
import zipfile
root = Path('Artifex')
output = Path(sys.argv[1]).resolve()
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(root.rglob('*')):
        if path.is_file():
            archive.write(path, path.as_posix())
print(output)
PY
)
