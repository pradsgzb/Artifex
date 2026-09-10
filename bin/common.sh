#!/usr/bin/env bash
set -euo pipefail

ARTIFEX_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"

artifex_find_python() {
  local candidate
  for candidate in \
    "$ARTIFEX_ROOT/.venv/bin/python3" \
    "$ARTIFEX_ROOT/.venv/bin/python" \
    "$ARTIFEX_ROOT/runtime/python/bin/python3" \
    "$ARTIFEX_ROOT/runtime/python/bin/python" \
    "$ARTIFEX_ROOT/runtime/python/python.exe"; do
    if [[ -x "$candidate" || -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  printf 'Artifex requires Python 3.10 or newer. Run ./setup.sh first.\n' >&2
  return 1
}

artifex_prepare_environment() {
  export PYTHONUTF8=1
  export PYTHONIOENCODING=utf-8
  export PYTHONNOUSERSITE=1
  export HF_HOME="${HF_HOME:-$ARTIFEX_ROOT/cache/huggingface}"
  export HF_HUB_CACHE="${HF_HUB_CACHE:-$HF_HOME/hub}"
  export TORCH_HOME="${TORCH_HOME:-$ARTIFEX_ROOT/cache/torch}"
  export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$ARTIFEX_ROOT/cache}"
  mkdir -p "$HF_HOME" "$HF_HUB_CACHE" "$TORCH_HOME" "$XDG_CACHE_HOME"
}

artifex_python() {
  local python
  python="${ARTIFEX_PYTHON:-$(artifex_find_python)}"
  printf '%s\n' "$python"
}
