#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"
source "$ROOT/bin/common.sh"
artifex_prepare_environment
PYTHON="$(artifex_python)"

printf 'Python: %s\n' "$($PYTHON --version 2>&1)"
"$PYTHON" -m compileall -q "$ROOT/qwen_archive" "$ROOT/qwen_archive.py"
"$PYTHON" - <<PY
from pathlib import Path
from qwen_archive.config import load_json_config
config = load_json_config(Path(r'''$ROOT/config/settings.json'''))
print('Configuration: valid')
print('Model:', config['model'])
PY
"$PYTHON" -m unittest discover -s "$ROOT/tests" -p 'test_*.py' -v

for script in "$ROOT"/*.sh "$ROOT"/bin/*.sh; do
  bash -n "$script"
done
if command -v node >/dev/null 2>&1; then
  node --check "$ROOT/web/static/app.js"
  NODE_PATH="$(npm root -g 2>/dev/null || true)" node "$ROOT/tools/check-typescript.mjs"
else
  printf "Node.js unavailable; JavaScript/TypeScript source checks skipped.\n"
fi
printf 'Verification completed successfully.\n'
