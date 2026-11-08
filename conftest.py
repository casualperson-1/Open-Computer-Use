"""Test bootstrap.

The app packages are normally importable through an editable install, but that
install can lag behind new subpackages. Adding the source roots explicitly keeps
`pytest` working from a clean checkout without reinstalling anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Later entries end up earlier on sys.path; `app`/`agent` win, and `shared`
# and `node` stay importable from the repository root.
for candidate in (ROOT / "node", ROOT, ROOT / "server", ROOT / "backend"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
