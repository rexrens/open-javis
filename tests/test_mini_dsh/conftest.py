"""Make examples/mini_dsh importable as top-level ``core`` / ``providers``."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "examples" / "mini_dsh"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
