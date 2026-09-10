#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

# The official Windows embeddable Python distribution runs in isolated
# ``._pth`` mode.  A portable Artifex folder may therefore not expose the
# directory containing this launcher on ``sys.path``.  Always add the
# application root before importing the adjacent qwen_archive package.
APP_ROOT = Path(__file__).resolve().parent
app_root_text = str(APP_ROOT)
if app_root_text not in sys.path:
    sys.path.insert(0, app_root_text)

from qwen_archive.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
