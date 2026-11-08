"""Server-side agent package.

Adds the repository root to ``sys.path`` so the installed ``open-computer-use``
console script can import ``shared`` and ``node`` regardless of its working
directory (console scripts start with the venv's bin directory on the path, not
the repository).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
