"""pytest bootstrap: make `src` importable regardless of invocation cwd."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
