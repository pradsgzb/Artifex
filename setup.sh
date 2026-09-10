#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
BUILD_WEB=0
INSTALL_OPTIONAL=0

usage() {
  cat <<'USAGE'
Usage: ./setup.sh [--python /path/to/python3] [--with-optional] [--build-web]

Creates an application-local virtual environment and installs Artifex runtime
and server dependencies. The local Qwen weights and ExifTool are not downloaded
implicitly; configure their paths in config/settings.json.
USAGE
}

while (($#)); do
  case "$1" in
    --python) PYTHON_BIN="${2:?Missing value for --python}"; shift 2 ;;
    --with-optional) INSTALL_OPTIONAL=1; shift ;;
    --build-web) BUILD_WEB=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && [[ ! -x "$PYTHON_BIN" ]]; then
  printf 'Python executable not found: %s\n' "$PYTHON_BIN" >&2
  exit 1
fi
"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit('Artifex requires Python 3.10 or newer.')
print(f'Using Python {sys.version.split()[0]}')
PY

"$PYTHON_BIN" -m venv "$ROOT/.venv"
PYTHON="$ROOT/.venv/bin/python"
"$PYTHON" -m pip install --upgrade pip setuptools wheel
"$PYTHON" -m pip install torch
"$PYTHON" -m pip install -r "$ROOT/requirements.txt" -r "$ROOT/requirements-server.txt"
if ((INSTALL_OPTIONAL)); then
  "$PYTHON" -m pip install -r "$ROOT/requirements-optional.txt"
fi

if ! command -v exiftool >/dev/null 2>&1; then
  printf '\nWARNING: ExifTool was not found on PATH. Install it with your platform package manager\n' >&2
  printf 'or set application.exifToolPath in config/settings.json.\n\n' >&2
fi

if ((BUILD_WEB)); then
  "$ROOT/bin/build-web.sh"
fi
"$ROOT/verify.sh"
printf '\nArtifex setup completed.\n'
