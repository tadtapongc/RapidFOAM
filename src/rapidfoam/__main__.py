"""Allow running as: python -m rapidfoam"""

import sys
from pathlib import Path

# Add src to path when running directly
src_dir = Path(__file__).resolve().parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from rapidfoam.cli import setup_main  # noqa: E402  (import after the src/ path insert above)

if __name__ == "__main__":
    setup_main()
