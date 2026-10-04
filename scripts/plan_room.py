"""Thin shim so the documented command works without installing.

The implementation lives in `scan2plan.cli` so that an installed copy exposes it
as the `scan2plan-plan` console script. This wrapper only puts `src/` on the
path, which is what lets `python scripts/plan_room.py capture.zip` work on a
fresh clone.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from scan2plan.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
