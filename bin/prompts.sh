#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
artifex_prepare_environment
exec "$(artifex_python)" "$ARTIFEX_ROOT/qwen_archive.py" prompts "$@"
