"""Run the provider-free GeoChange MVP evaluation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evaluation.geochange import main  # noqa: E402

raise SystemExit(main())
