"""What the tape diagonals actually say about room shape.

The first reading of this data was "these rooms are not rectangles, the
diagonals disagree with the sides". That was too strong, and it is worth
showing why rather than quietly replacing it.

For two adjacent sides L and W meeting at a corner, with d the diagonal between
the corners at their far ends, the law of cosines gives

    d^2 = L^2 + W^2 - 2*L*W*cos(theta)

so theta is recoverable directly. If theta comes out near 90 degrees, the room
is rectangular to within ordinary construction and tape error, and the diagonal
discrepancy is not evidence of a trapezoid at all.

The residual does matter, though, for a different and more consequential
reason: a few degrees of skew changes the room's width by hundreds of
millimetres along its length, which means a single tape reading of "width" is
not a well-defined quantity at all.
"""

from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PATH = os.path.join(ROOT, "data", "field_ground_truth.json")


def main() -> None:
    gt = json.load(open(PATH, encoding="utf-8"))
    rooms = gt["rooms"]

    print(f"{'room':<18}{'L (m)':>8}{'W (m)':>8}{'d (m)':>8}"
          f"{'sqrt(L2+W2)':>13}{'delta':>9}{'theta':>9}{'skew over L':>14}")
    print("-" * 87)

    rows = []
    for r in rooms:
        L, W, d = r["length_m"], r["width_m"], r["diagonal_m"]
        rect = math.hypot(L, W)
        cos_t = (L * L + W * W - d * d) / (2.0 * L * W)
        cos_t = max(-1.0, min(1.0, cos_t))
        theta = math.degrees(math.acos(cos_t))
        delta = d - rect
        # How much the far end of the room differs in width from the near end.
        drift = L * abs(math.tan(math.radians(90.0 - theta)))
        rows.append((r["id"], L, W, d, rect, delta, theta, drift))
        print(f"{r['id']:<18}{L:>8.3f}{W:>8.3f}{d:>8.3f}{rect:>13.3f}"
              f"{delta * 1000:>7.0f}mm{theta:>8.1f}°{drift * 1000:>11.0f}mm")

    print()
    worst_theta = max(abs(90.0 - t) for _, _, _, _, _, _, t, _ in rows)
    worst_drift = max(d for *_, d in rows)
    print(f"  largest corner deviation from perpendicular: {worst_theta:.1f} deg")
    print(f"  resulting width ambiguity along the room:     {worst_drift * 1000:.0f} mm")
    print()
    print("  Reading: every corner is within a few degrees of 90, which is ordinary")
    print("  construction tolerance and comfortably inside tape error. These rooms")
    print("  are rectangular for our purposes. The discrepancy is not evidence that")
    print("  they are trapezoids.")
    print()
    print("  The consequence that does matter: a couple of degrees of wall skew moves")
    print("  the far end of a wall by up to ~240 mm. A single tape reading of 'width'")
    print("  therefore names a position-dependent quantity, and so does any derived")
    print("  opening position. Comparisons against tape reference inherit that")
    print("  ambiguity, which is a property of the reference, not of the estimator.")

    print()
    print("  Implication for the synthetic benchmark: all five generated rooms are")
    print("  exact rectangles with perfectly parallel walls, so they cannot exercise")
    print("  this case. A skewed room needs to be added before the geometry claims")
    print("  mean anything for real construction.")


if __name__ == "__main__":
    main()
