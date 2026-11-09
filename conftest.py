"""Test bootstrap.

The app packages are normally importable through an editable install, but that
install can lag behind new subpackages. Adding the source roots explicitly keeps
`pytest` working from a clean checkout without reinstalling anything.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Never let a test import the app against the real ./data directory: the app's
# lifespan applies migrations, so an unguarded import would rewrite the user's
# database. Modules that need their own database override these before importing
# `app`.
_TEST_STATE = tempfile.mkdtemp(prefix="ocu-test-state-")
os.environ.setdefault("OPEN_COMPUTER_USE_DATA_DIR", _TEST_STATE)
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TEST_STATE}/test.db")

ROOT = Path(__file__).resolve().parent

# Later entries end up earlier on sys.path; `app`/`agent` win, and `shared`
# and `node` stay importable from the repository root.
for candidate in (ROOT / "node", ROOT, ROOT / "server"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
