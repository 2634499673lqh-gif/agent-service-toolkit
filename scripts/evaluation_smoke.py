"""CI entry point for the provider-free Phase 8 Evaluation smoke."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evaluation.smoke import main

raise SystemExit(main())
