#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
WEB_ROOT="$ROOT/web/angular"
if ! command -v npm >/dev/null 2>&1; then
  printf 'npm is required to build the Angular application.\n' >&2
  exit 1
fi
cd "$WEB_ROOT"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build
printf 'Angular bundle created under %s/web/dist/artifex-chat.\n' "$ROOT"
