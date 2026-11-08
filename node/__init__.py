"""The local desktop node.

The node owns the mouse, keyboard, screen and browser on one machine. It dials
out to the server over WebSocket, so the server never needs an inbound port on
the machine being controlled and the machine can sit behind NAT.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `shared` lives next to this package at the repository root. The node may be
# started from any working directory, so put the root on sys.path explicitly.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

__all__ = ["client", "config", "control", "drivers", "executor", "safety"]
