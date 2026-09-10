#!/usr/bin/env bash
set -euo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
artifex_prepare_environment
cd "$ARTIFEX_ROOT"
exec "$(artifex_python)" -m qwen_archive.server_cli "$@"
