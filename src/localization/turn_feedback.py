"""Filter camera gain observations without learning from isolated outliers."""

import math
from statistics import median


def filtered_turn_gain(configured, current, measured, samples):
    magnitude = abs(configured)
    value = abs(measured)
    if not math.isfinite(value) or not magnitude * 0.05 <= value <= magnitude * 5:
        return current
    samples.append(value)
    del samples[:-3]
    cluster = [v for v in samples if abs(v - value) <= 0.2 * value]
    # Normal calibration errors can close the loop on the first clean sample.
    # Larger changes and changes to a learned gain need corroboration.
    if len(cluster) >= 2:
        estimate = median(cluster)
    elif len(samples) == 1 and magnitude * 0.5 <= value <= magnitude * 2:
        estimate = value
    else:
        return current
    return math.copysign(estimate, configured)
