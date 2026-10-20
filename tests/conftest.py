"""Put the repo root on sys.path so tests can import project packages
(agents, core, connectors, shadow_box) without an install step."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
